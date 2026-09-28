"""Bounded post-control native comparison. Never imports or calls live MT5 code.

The clean subprocess replays all three independent engines before this process
opens capture manifests or imports observed auditors. Native facts and execution
hypothesis differences are separate; neither authorizes strategy search.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
COMPARISON_SOURCES = (
    "tools/compare_simulator_forward.py",
    "tools/compare_causal_controls.py",
    "research/gold_iterative/ledger_evidence.py",
    "research/gold_iterative/exit_execution.py",
    "tools/audit_causal_lineage.py",
    "tools/audit_management_capture.py",
    "mt5_deal_reason.py",
    "research/causal_comparison.py",
    "research/causal_replay.py",
    "tools/prepare_simulator_forward.py",
    "tools/run_causal_controls.py",
    "tools/run_protection_controls.py",
)
MAX_BYTES = 16_000_000
MAX_ROWS = 100_000
MAX_CAPTURES = 8
MAX_SECONDS = 600
MONEY_FIELDS = ("profit", "swap", "commission", "fee")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _encode(value):
    def default(item):
        if isinstance(item, datetime):
            return item.isoformat()
        if isinstance(item, Decimal):
            return str(item)
        raise TypeError(type(item).__name__)
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, default=default).encode()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode(raw):
    def invalid(value):
        raise ValueError(f"nonfinite JSON: {value}")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=invalid)


def _path(path):
    result = Path(path).resolve()
    if not result.is_relative_to(ROOT.resolve()):
        raise ValueError("comparison inputs and outputs must remain in the workspace")
    return result


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read(path, watched, *, raw=False):
    path = _path(path)
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"bounded input exceeded: {path.name}")
    sha = hashlib.sha256(data).hexdigest()
    if path in watched and watched[path] != sha:
        raise ValueError(f"input changed after independent validation: {path.name}")
    watched[path] = sha
    return (data if raw else _decode(data)), {"path": str(path), "sha256": sha}


def _utc(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("explicit UTC offset required")
    return parsed.astimezone(timezone.utc)


def _number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("native number missing")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid native number") from exc
    if not result.is_finite():
        raise ValueError("nonfinite native number")
    return result


def _money(value):
    return _number(value).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def _broker_time(row, offset, *, order=False):
    field = "time_done" if order else "time"
    seconds, millis = row.get(field), row.get(field + "_msc")
    if type(seconds) is not int or type(millis) is not int or millis < 0 or millis // 1000 != seconds:
        raise ValueError("native second/millisecond timestamp mismatch")
    return EPOCH + timedelta(milliseconds=millis, seconds=-offset)


def _independent(protocol, dataset, results):
    # -I removes ambient PYTHONPATH/site customization; only this project is added.
    script = """
import hashlib, json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from tools import prepare_simulator_forward as f
watched = {}
read, proof = f._read, f._proof
def watched_read(path, local):
    value = read(path, local)
    watched.update(local)
    return value
def watched_proof(value, local):
    result = proof(value, local)
    watched.update(local)
    return result
f._read, f._proof = watched_read, watched_proof
report = f.check(sys.argv[2], sys.argv[3], stage='independent', results=sys.argv[4])
protocol, _ = f._read(sys.argv[2], watched)
for name in json.loads(sys.argv[5]):
    path = pathlib.Path(sys.argv[1]) / name
    f._producer({'path':str(path), 'sha256':f.protection.digest(path)}, protocol, watched)
if any(name in sys.modules for name in f.protection.FORBIDDEN_IMPORTS):
    raise ValueError('forbidden observed/live module imported in independent child')
print(json.dumps({'report':report, 'inputs':{str(k):v for k,v in watched.items()}}, default=str))
"""
    finished = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(ROOT), str(protocol), str(dataset),
         str(results), json.dumps(COMPARISON_SOURCES)],
        cwd=ROOT, capture_output=True, timeout=MAX_SECONDS, check=False,
    )
    if finished.returncode != 0:
        raise ValueError("independent subprocess validation failed: " +
                         finished.stderr.decode(errors="replace")[-3000:])
    if len(finished.stdout) > MAX_BYTES:
        raise ValueError("independent subprocess output exceeded budget")
    return _decode(finished.stdout)


def _manifest_file(manifest, path, name, watched, *, raw=False):
    proof = manifest.get("files", {}).get(name)
    if not isinstance(proof, dict):
        raise ValueError(f"capture file proof missing: {name}")
    value, actual = _read(path.parent / name, watched, raw=raw)
    if actual["sha256"] != proof.get("sha256"):
        raise ValueError(f"capture file hash mismatch: {name}")
    if (path.parent / name).stat().st_size != proof.get("bytes"):
        raise ValueError(f"capture file size mismatch: {name}")
    return value


def _capture(manifests, dataset, dataset_path, watched, *, capture_contract=None):
    """Normalize bounded native deltas; no historical session auditor is imported."""
    from research.causal_replay import RAW_FIELDS
    start, cutoff = _utc(dataset["start_utc"]), _utc(dataset["cutoff_utc"])
    end = _utc("2026-09-09T08:30:00+00:00")
    events, context_rows, proofs, blockers, previous = [], [], [], [], None
    account_id = None
    latest = None
    for name in manifests:
        path = _path(name)
        manifest, proof = _read(path, watched)
        proofs.append(proof)
        if (manifest.get("contract") != "morning_causal_capture_v1"
                or _utc(manifest["window_start_utc"]) != start
                or _utc(manifest["window_end_exclusive_utc"]) != end
                or not start - timedelta(hours=6) <= _utc(manifest["event_cutoff_utc"]) <= cutoff + timedelta(seconds=120)
                or manifest.get("raw_server_epoch_minus_utc_seconds") != 10800
                or manifest.get("read_only") is not True
                or manifest.get("orders_sent_by_collector") != 0):
            raise ValueError("native capture window or clock contract mismatch")
        account = manifest.get("account", {})
        identity = account.get("account_identity_sha256")
        if (account.get("currency") != "EUR" or account.get("terminal_connected") is not True
                or not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity)):
            raise ValueError("native account EUR/identity evidence missing")
        if account_id is not None and account_id != identity:
            raise ValueError("native account changed across captures")
        account_id = identity
        if capture_contract is not None:
            live = manifest.get("live_code", {})
            checks = {"collector_sha256": live.get("collector_sha256"),
                      "wrapper_sha256": live.get("wrapper_sha256"),
                      "expected_live_commit": live.get("commit"),
                      "expected_account_identity_sha256": identity}
            if (live.get("clean") is not True
                    or any(not capture_contract.get(key) or capture_contract[key] != value for key, value in checks.items())):
                raise ValueError("native capture differs from frozen collector/live/account contract")
        chain = manifest["event_evidence"]
        if previous is not None:
            if (chain.get("anchor_manifest_sha256") != previous["sha256"]
                    or chain.get("anchor_prefix_bytes") != latest["event_evidence"].get("prefix_end_bytes")
                    or _utc(manifest["event_cutoff_utc"]) < _utc(latest["event_cutoff_utc"])):
                raise ValueError("native capture delta chain is not contiguous")
        if chain.get("invalid_delta_rows"):
            blockers.append("native_capture_invalid_delta_rows")
        blockers.extend(f"capture:{item}" for item in manifest.get("blockers", []))
        if previous is None:
            context = _manifest_file(manifest, path, "prior_context.json", watched)
            if not isinstance(context, dict):
                raise ValueError("native prior context must be an event mapping")
            context_rows = [value for value in context.values() if isinstance(value, dict)]
        compressed = _manifest_file(manifest, path, "event_delta.jsonl.gz", watched, raw=True)
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or len(raw) != chain.get("delta_bytes"):
            raise ValueError("bounded native delta size mismatch")
        if hashlib.sha256(raw).hexdigest() != chain.get("delta_sha256"):
            raise ValueError("native decompressed delta hash mismatch")
        lines = [line for line in raw.splitlines() if line.strip()]
        if len(lines) != chain.get("delta_rows") or len(events) + len(lines) > MAX_ROWS:
            raise ValueError("bounded native event denominator mismatch")
        events.extend(_decode(line) for line in lines)
        previous, latest = proof, manifest
    if min(_utc(latest["event_cutoff_utc"]), end) != cutoff:
        raise ValueError("native capture and independent cutoff differ")
    source = dataset.get("source_evidence", {})
    if capture_contract is not None:
        source_capture = source.get("capture_manifest", {})
        if (source_capture.get("sha256") != proofs[-1]["sha256"]
                or _path(source_capture.get("path", "")) != _path(manifests[-1])):
            raise ValueError("native capture chain differs from independent source_capture")
    for label in ("metadata", "clock"):
        spec = source.get(label, {})
        value, proof = _read(spec["path"], watched)
        if proof["sha256"] != spec.get("sha256"):
            raise ValueError(f"independent {label} proof mismatch")
        if capture_contract is not None and value.get("source_capture_sha256") != proofs[-1]["sha256"]:
            raise ValueError(f"independent {label} came from a different source_capture")
        if label == "metadata":
            if value.get("account_currency") != "EUR":
                raise ValueError("independent/native account currency mismatch")
            aliases = {"contract_size": "trade_contract_size", "stops_level_points": "trade_stops_level",
                       "freeze_level_points": "trade_freeze_level"}
            for symbol in ("XAUUSD", "EURUSD"):
                expected = value.get("symbols", {}).get(symbol, {})
                native = latest.get("symbol_contract", {}).get(symbol, {})
                if not expected or any(native.get(aliases.get(key, key)) != item for key, item in expected.items()):
                    raise ValueError(f"independent/native symbol metadata mismatch: {symbol}")
        if label == "clock" and value.get("broker_epoch_offset_seconds") != 10800:
            raise ValueError("independent/native clock mismatch")
    # Context copies may duplicate actual journal rows. Do not deduplicate the
    # journal itself, since that would hide repeated runtime event identities.
    delta_copies = {_encode(row) for row in events}
    events = [row for row in context_rows if _encode(row) not in delta_copies] + events
    source_event_count = len(events)
    for row in events:
        if not isinstance(row, dict) or "ts" not in row:
            raise ValueError("malformed native journal event")
        if _utc(row["ts"]) > _utc(latest["event_cutoff_utc"]):
            raise ValueError("native event after capture source cutoff")
    events = [row for row in events if _utc(row["ts"]) <= cutoff]
    projected = [{key: row.get(key) for key in RAW_FIELDS} for row in events
                 if row.get("ev") == "telegram_raw" and start <= _utc(row["ts"]) <= cutoff
                 and _utc(row["ts"]) < end]
    projected.sort(key=lambda row: _utc(row["ts"]))
    independent_messages, _ = _read(dataset_path / "raw_messages.json", watched)
    if _encode(projected) != _encode(independent_messages):
        raise ValueError("native capture causal messages differ from prevalidated independent inputs")
    path = _path(manifests[-1])
    deals_payload = _manifest_file(latest, path, "broker_deals.json", watched)
    orders_payload = _manifest_file(latest, path, "broker_orders.json", watched)
    deals, orders = deals_payload.get("rows"), orders_payload.get("rows")
    for rows in (deals, orders):
        if not isinstance(rows, list) or len(rows) > MAX_ROWS or any(not isinstance(row, dict) for row in rows):
            raise ValueError("bounded broker history shape invalid")
    broker = latest.get("broker_evidence", {})
    if broker.get("deal_rows") != len(deals) or broker.get("order_rows") != len(orders):
        raise ValueError("native broker history denominator mismatch")
    for rows, is_order, field in ((deals, False, "time_utc"), (orders, True, "time_done_utc")):
        for row in rows:
            # Pending orders have no done time. They are retained in source
            # counts but cannot be used as completed native fill evidence.
            if is_order and row.get("time_done_msc") == 0 and row.get(field) is None:
                continue
            if _utc(row.get(field)) != _broker_time(row, 10800, order=is_order):
                raise ValueError("native normalized UTC contradicts raw broker milliseconds")
    scoped_positions = {row.get("position_id") for row in deals if start <= _broker_time(row, 10800) <= cutoff}
    scoped_deals = [row for row in deals if row.get("position_id") in scoped_positions
                    and _broker_time(row, 10800) <= cutoff]
    scoped_order_ids = {row.get("order") for row in scoped_deals}
    scoped_orders = [row for row in orders if row.get("ticket") in scoped_order_ids
                     and _broker_time(row, 10800, order=True) <= cutoff]
    return {"events": events, "deals": scoped_deals, "orders": scoped_orders,
            "source_counts": {"deals": len(deals), "orders": len(orders), "events": source_event_count,
                              "events_after_effective_cutoff": source_event_count - len(events)},
            "capture_proofs": proofs, "account_identity_sha256": account_id,
            "account_currency": "EUR", "offset": 10800, "blockers": blockers}


def _ledgers(deals, selected, offset):
    from research.gold_iterative.ledger_evidence import ledger_ticket_evidence
    by_position, owners = defaultdict(list), {}
    blockers, excluded, seen = [], [], set()
    for deal in deals:
        ticket, position = deal.get("ticket"), deal.get("position_id")
        if type(ticket) is not int or ticket <= 0 or ticket in seen or type(position) is not int or position <= 0:
            blockers.append("native_deal_identity_invalid_or_duplicate")
        seen.add(ticket)
        by_position[str(position)].append(deal)
    baskets = {sig: [] for sig in selected}
    for position_id, rows in by_position.items():
        incoming = [row for row in rows if row.get("entry") == 0]
        if len(incoming) != 1:
            if all(row.get("magic") != 20260422 for row in rows):
                excluded.append({"position_id": position_id, "signal_id": None,
                                 "reason": "foreign_position_opening_outside_retained_history"})
                continue
            blockers.append(f"native_opening_count_unbound:{position_id}")
            continue
        opening = incoming[0]
        match = re.fullmatch(r"c2_([1-9]\d*)(?:_B([1-4]))?_g55", str(opening.get("comment", "")))
        owner = f"canal2_{match[1]}" if match else None
        if owner not in baskets:
            excluded.append({"position_id": position_id, "signal_id": owner,
                             "reason": "outside_selected_cohort" if owner else "native_owner_unbound"})
            if owner is None and opening.get("magic") == 20260422:
                blockers.append(f"gold_native_owner_unbound:{position_id}")
            continue
        owners[position_id] = owner
        outgoing = sorted([row for row in rows if row.get("entry") in (1, 3)], key=lambda row: row["time_msc"])
        enriched = [dict(row, time_utc=(EPOCH + timedelta(seconds=row["time"])).isoformat()) for row in rows]
        open_deal = next(row for row in enriched if row["ticket"] == opening["ticket"])
        close_deal = next((row for row in enriched if outgoing and row["ticket"] == outgoing[0]["ticket"]), None)
        components = {}
        for name in MONEY_FIELDS:
            try:
                components[name] = sum((_money(row.get(name)) for row in rows), Decimal(0))
            except ValueError:
                components[name] = None
        net = sum(components.values(), Decimal(0)) if all(value is not None for value in components.values()) else None
        components["net"] = net
        closed_volume = sum((_number(row["volume"]) for row in outgoing), Decimal(0))
        position = {"position_id": int(position_id), "ticket": opening["ticket"],
                    "direction": {0: "BUY", 1: "SELL"}.get(opening.get("type")), "symbol": opening.get("symbol"),
                    "open_price": opening.get("price"), "volume": opening.get("volume"),
                    "open_dt_utc": _broker_time(opening, offset).replace(microsecond=0),
                    "close_dt_utc": _broker_time(outgoing[0], offset).replace(microsecond=0) if outgoing else None,
                    "close_price": outgoing[0].get("price") if outgoing else None,
                    "is_closed": bool(outgoing) and closed_volume == _number(opening["volume"]),
                    "mt5_time_offset_s": offset, "pnl_net": net, "pnl_components": components,
                    "open_deal": open_deal, "close_deal": close_deal, "deals": enriched}
        baskets[owner].append(position)
        if any(row.get("magic") != 20260422 or row.get("symbol") != "XAUUSD" for row in rows):
            blockers.append(f"native_symbol_or_magic_mismatch:{position_id}")
    output = {}
    for sig, positions in baskets.items():
        nets = [row["pnl_net"] for row in positions]
        actual = {"sig_id": sig, "channel": "canal2", "symbol": "XAUUSD", "account_currency": "EUR",
                  "direction": positions[0]["direction"] if positions else None,
                  "mt5_time_offset_s": offset, "positions": positions, "n_positions": len(positions),
                  "reconciled_ok": bool(positions),
                  "pnl_real_mt5": sum(nets, Decimal(0)) if positions and all(value is not None for value in nets) else None}
        evidence, issues = ledger_ticket_evidence(actual)
        # This input flag means the native totals were constructed, not admitted.
        actual["reconciled_ok"] = not issues
        output[sig] = {"actual": actual, "ticket_evidence": evidence, "blockers": list(issues)}
    return output, owners, blockers, excluded


def _open_bindings(events, deals, orders, owners, offset):
    """Bind real successful OPEN_MARKET attempts, never retcode-only fills."""
    order_map = {row.get("ticket"): row for row in orders}
    openings = {row["ticket"]: row for row in deals if row.get("entry") == 0 and str(row.get("position_id")) in owners}
    attempts = [row for row in events if row.get("ev") == "mt5_action_attempt" and row.get("operation") == "OPEN_MARKET"]
    rows, seen = [], set()
    for event in attempts:
        if event.get("sig") not in set(owners.values()):
            continue
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        if result.get("deal") in (0, None) and result.get("retcode") not in (10009, 10010):
            continue
        deal_id, issues = result.get("deal"), []
        deal = openings.get(deal_id, {})
        request = event.get("request") if isinstance(event.get("request"), dict) else {}
        order = order_map.get(result.get("order"), {})
        if (not deal or deal_id in seen or event.get("broker_request_sent") is not True
                or result.get("retcode") != 10009
                or owners.get(str(deal.get("position_id"))) != event.get("sig")
                or deal.get("order") != result.get("order")
                or order.get("position_id") != deal.get("position_id")
                or sum(row.get("order") == deal.get("order") for row in deals) != 1):
            issues.append("opening_request_native_identity_mismatch")
        for field in ("symbol", "magic", "type"):
            if request.get(field) is None or any(row.get(field) != request[field] for row in (deal, order)):
                issues.append(f"opening_native_{field}_mismatch")
        try:
            if (request.get("action") != 1 or _number(result.get("price")) != _number(deal.get("price"))
                    or any(_number(row.get("volume")) != _number(deal.get("volume")) for row in (request, result))
                    or _number(order.get("volume_initial")) != _number(deal.get("volume"))
                    or _number(order.get("volume_current")) != 0 or order.get("state") != 4
                    or not _utc(event["broker_request_started_utc"]) <= _broker_time(deal, offset)
                    <= _broker_time(order, offset, order=True) <= _utc(event["broker_response_received_utc"])
                    <= _utc(event["ts"])):
                issues.append("opening_native_facts_or_clock_mismatch")
        except (KeyError, ValueError, TypeError):
            issues.append("opening_native_facts_missing")
        seen.add(deal_id)
        rows.append({"signal_id": event.get("sig"), "deal_ticket": deal_id,
                     "attempt_id": event.get("attempt_id"), "blockers": issues})
    for deal_id in sorted(set(openings) - seen):
        rows.append({"signal_id": owners[str(openings[deal_id]["position_id"])], "deal_ticket": deal_id,
                     "blockers": ["opening_without_native_bound_attempt"]})
    return rows


def _installed_state(events, sig, ticket_evidence, *, lineage_ok):
    """Verify an observed positions_get state, never its server install instant."""
    snapshots = [row for row in events if row.get("sig") == sig and row.get("ev") == "mt5_position_snapshot"
                 and row.get("after_action") == "MODIFY_SLTP"]
    attempts = [row for row in events if row.get("sig") == sig and row.get("ev") == "mt5_action_attempt"
                and row.get("operation") == "MODIFY_SLTP"]
    requests = [row for row in events if row.get("sig") == sig and row.get("ev") == "mt5_modify_requested"]
    observations, blockers, covered, ambiguous = [], [], set(), []
    def same(left, right):
        return left is None and right is None or left is not None and right is not None and _number(left) == _number(right)
    for snapshot in snapshots:
        if snapshot.get("position_exists") is not True:
            continue
        ticket = str(snapshot.get("ticket"))
        matches = [row for row in attempts if row.get("attempt_id") == snapshot.get("attempt_id")]
        roots = [row for row in requests if row.get("action_id") == snapshot.get("action_id")]
        reason = None
        try:
            if len(matches) != 1 or len(roots) != 1 or ticket not in ticket_evidence:
                raise ValueError("snapshot_lineage_or_ticket_unbound")
            attempt, root = matches[0], roots[0]
            request, result = attempt.get("request", {}), attempt.get("result", {})
            if (not lineage_ok or snapshot.get("snapshot_error") or snapshot.get("retcode") not in (10009, 10025)
                    or result.get("retcode") != snapshot.get("retcode")
                    or attempt.get("broker_request_sent") is not True or request.get("action") != 6
                    or str(request.get("position")) != ticket or str(attempt.get("ticket")) != ticket
                    or str(root.get("ticket")) != ticket):
                raise ValueError("snapshot_request_or_result_contradiction")
            for field in ("action_id", "decision_id", "message_revision_id", "action_revision"):
                value = snapshot.get(field)
                if value is None or value == "" or attempt.get(field) != value or root.get(field) != value:
                    raise ValueError("snapshot_intent_revision_contradiction")
            if type(snapshot.get("action_revision")) is not int:
                raise ValueError("snapshot_revision_invalid")
            for level in ("sl", "tp"):
                applied = _number(request.get(level))
                if applied < 0 or not same(snapshot.get(level), applied):
                    raise ValueError(f"snapshot_applied_{level}_contradiction")
                intended = snapshot.get("requested_" + level)
                allowed = [root.get("new_" + level)]
                if attempt.get("preflight_effective_" + level) is not None:
                    allowed.append(attempt["preflight_effective_" + level])
                if level == "sl" and attempt.get("preflight_deferred_sl") is not None:
                    allowed.append(attempt.get("preflight_deferred_sl"))
                if not any(same(intended, item) for item in allowed):
                    raise ValueError(f"snapshot_intended_{level}_contradiction")
            facts = ticket_evidence[ticket]
            observed_at = _utc(snapshot["ts"])
            interval_start = _utc(attempt["broker_response_received_utc"])
            if (not _utc(root["ts"]) <= interval_start <= observed_at
                    or observed_at < _utc(facts["opened_at"])
                    or facts.get("closed_at") is not None and _utc(facts["closed_at"]) < interval_start):
                raise ValueError("snapshot_native_chronology_contradiction")
            if (snapshot.get("symbol") != facts["symbol"] or snapshot.get("magic") != 20260422
                    or snapshot.get("position_type") != {"BUY": 0, "SELL": 1}.get(facts["direction"])
                    or not same(snapshot.get("price_open"), facts["open_price"])
                    or not 0 < _number(snapshot.get("volume")) <= _number(facts["volume"])):
                raise ValueError("snapshot_native_position_contradiction")
            if any(interval_start <= _utc(row["closed_at"]) <= observed_at for row in facts["exit_deals"]):
                ambiguous.append({"ticket": ticket, "attempt_id": snapshot["attempt_id"],
                                  "interval_start": interval_start, "interval_end": observed_at})
                continue
            remaining = _number(facts["volume"]) - sum((_number(row["volume"]) for row in facts["exit_deals"]
                if _utc(row["closed_at"]) < interval_start), Decimal(0))
            if not same(snapshot.get("volume"), remaining):
                raise ValueError("snapshot_native_position_contradiction")
            covered.add(ticket)
            observations.append({"ticket": ticket, "action_id": snapshot["action_id"],
                "attempt_id": snapshot["attempt_id"], "action_revision": snapshot["action_revision"],
                "observed_interval_start": interval_start, "snapshot_logged_at": snapshot["ts"],
                "sl": snapshot["sl"], "tp": snapshot["tp"],
                "scope": "observed_state", "server_install_time": None})
        except (ValueError, TypeError, KeyError) as exc:
            reason = f"installed_state:{ticket}:{exc}"
        if reason:
            blockers.append(reason)
    missing = sorted(set(ticket_evidence) - covered)
    gaps = [f"installed_protection_snapshot_missing:{ticket}" for ticket in missing]
    if ambiguous:
        gaps.append("snapshot_time_ambiguous")
    if not ticket_evidence:
        gaps.append("installed_protection_snapshot_missing")
    return {"status": "blocked" if blockers else "not_observed" if gaps else "verified",
            "scope": "observed_state", "server_install_time": None, "observations": observations,
            "ambiguous_snapshots": ambiguous,
            "coverage_gaps": gaps, "blockers": blockers, "evidence": []}


def _observed(capture, selected, results):
    from tools.audit_causal_lineage import audit_rows
    from tools.audit_management_capture import audit_capture
    from research.gold_iterative.exit_execution import bind_close_attempts_to_deals
    from tools.compare_causal_controls import observed_events, simulated_events
    from research.causal_comparison import compare_sequences
    events, deals, orders = (capture[key] for key in ("events", "deals", "orders"))
    journal = audit_rows(events, source_sha256=hashlib.sha256(_encode(events)).hexdigest())
    management = audit_capture(events)
    ledgers, owners, ledger_blockers, excluded = _ledgers(deals, selected, capture["offset"])
    closes = bind_close_attempts_to_deals(events=events, broker_deals=deals, broker_orders=orders,
                                        position_signal=owners, mt5_time_offset_s=capture["offset"])
    opens = _open_bindings(events, deals, orders, owners, capture["offset"])
    global_issues = list(capture["blockers"]) + ledger_blockers
    if journal.get("summary", {}).get("blocked", 0):
        global_issues.append("native_causal_journal_blocked")
    if management["status"] == "blocked":
        global_issues.append("native_management_capture_blocked")
    if len({row.get("ticket") for row in orders}) != len(orders):
        global_issues.append("native_order_identity_duplicate")
    independent = {row["signal_id"]: row for row in results["results"]}
    rows = []
    for sig in selected:
        basket = ledgers[sig]
        positions = basket["actual"]["positions"]
        tickets = {str(row["position_id"]) for row in positions}
        signal_events = [row for row in events if row.get("sig") == sig]
        issues = list(global_issues) + list(basket["blockers"])
        open_issues = [issue for row in opens if row["signal_id"] == sig for issue in row["blockers"]]
        close_issues = [issue for row in closes["exit_rows"] if row["position_id"] in tickets for issue in row["blockers"]]
        close_issues += [issue for row in closes["attempts"] if row["signal_id"] == sig for issue in row["blockers"]]
        requests = [row for row in signal_events if row.get("ev") in
                    {"mt5_order_requested", "mt5_modify_requested", "mt5_close_requested", "mt5_cancel_requested"}]
        attempts = [row for row in signal_events if row.get("ev") == "mt5_action_attempt"]
        request_ok = bool(requests) and not global_issues
        attempt_ok = bool(attempts) and request_ok and all(
            isinstance(row.get("result"), dict) and type(row["result"].get("retcode")) is int
            for row in attempts)
        installed = _installed_state(events, sig, basket["ticket_evidence"], lineage_ok=not global_issues)
        accounting_ok = bool(positions) and not basket["blockers"] and not ledger_blockers
        binding_ok = accounting_ok and not open_issues and not close_issues and not global_issues
        issues.extend(open_issues + close_issues)
        if not positions:
            issues.append("observed_no_position_evidence")
        issues.extend(installed["blockers"])
        checks = {"requests": request_ok, "accepted_or_rejected": attempt_ok,
                  "native_deals": accounting_ok,
                  "native_accounting": accounting_ok, "request_native_binding": binding_ok}
        differences = []
        sequence = None
        if accounting_ok:
            try:
                sequence = compare_sequences(observed_events(basket["actual"]),
                    simulated_events(independent[sig]["scalar"], basket["actual"]["direction"]))
                differences = [item for item in sequence["comparisons"] if item["differences"]]
            except ValueError as exc:
                issues.append(f"hypothesis_sequence_unavailable:{exc}")
        rows.append({"signal_id": sig, "status": "differences_for_review" if differences else "reviewed",
                     "observed_entries": len(positions),
                     "observed_exits": sum(len(value["exit_deals"]) for value in basket["ticket_evidence"].values()),
                     "observed_counts_complete": bool(positions) and not global_issues,
                     "native_factual_review_status": "complete" if binding_ok else "incomplete",
                     "capability_gaps": ["server_protection_installation_instant_unobserved"],
                     "coverage_gaps": installed["coverage_gaps"],
                     "observed_net_eur": basket["actual"]["pnl_real_mt5"] if accounting_ok else None,
                     "blockers": sorted(set(issues)), "differences": differences,
                     "lifecycle_checks": {key: {"status": ("exact" if key in
                          {"native_accounting", "request_native_binding"} else "verified") if ok else "blocked",
                          "evidence": []} for key, ok in checks.items()},
                     "sequence_comparison": sequence})
        rows[-1]["lifecycle_checks"]["installed_protection"] = installed
    return {"rows": rows, "ledgers": ledgers, "journal": journal, "management": management,
            "open_bindings": opens, "close_bindings": closes, "excluded_native_positions": excluded}


def _native_process(capture, selected, results, *, remaining):
    payload = _encode({"capture": capture, "selected": selected, "results": results})
    if len(payload) > MAX_BYTES or remaining <= 0:
        raise ValueError("native subprocess input/time budget exceeded")
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from tools import compare_simulator_forward as c
value = c._decode(sys.stdin.buffer.read(c.MAX_BYTES + 1))
result = c._observed(value['capture'], value['selected'], value['results'])
for name in ('MetaTrader5','executor','listener','gold_555_live_candidate','dubai_live_candidate'):
    if name in sys.modules:
        raise ValueError('live module imported by native auditor')
sys.stdout.buffer.write(c._encode(result))
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(ROOT)],
                            cwd=ROOT, input=payload, capture_output=True,
                            timeout=remaining, check=False)
    if result.returncode:
        raise ValueError("native audit subprocess failed: " + result.stderr.decode(errors="replace")[-3000:])
    if len(result.stdout) > MAX_BYTES:
        raise ValueError("native audit output exceeded budget")
    return _decode(result.stdout)


def _blocked_native(selected, reason):
    checks = ("requests", "accepted_or_rejected", "installed_protection", "native_deals",
              "native_accounting", "request_native_binding")
    return {"native_preparation_error": reason, "rows": [
        {"signal_id": sig, "status": "blocked", "observed_entries": 0, "observed_exits": 0,
         "observed_counts_complete": False, "observed_net_eur": None, "blockers": [reason],
         "native_factual_review_status": "incomplete", "capability_gaps": ["native_evidence_unavailable"],
         "coverage_gaps": ["native_evidence_unavailable"],
         "differences": [], "sequence_comparison": None,
         "lifecycle_checks": {key: {"status": "blocked", "evidence": [],
             **({"scope": "observed_state", "server_install_time": None} if key == "installed_protection" else {})}
             for key in checks}}
        for sig in selected]}


def compare(protocol_path, dataset_path, results_path, *, manifests, out):
    started = time.monotonic()
    if not 1 <= len(manifests) <= MAX_CAPTURES:
        raise ValueError("capture count must be 1..8")
    protocol_path, dataset_path, results_path, out = map(_path, (protocol_path, dataset_path, results_path, out))
    if out.exists():
        raise ValueError("comparison output already exists; never overwrite")
    gate = _independent(protocol_path, dataset_path, results_path)
    if gate["report"].get("blockers") or "independent_results_missing" in gate["report"].get("incomplete", []):
        return gate["report"] | {"observed_reads": False, "comparison": None}
    watched = {_path(path): sha for path, sha in gate["inputs"].items()}
    for path, sha in watched.items():
        if _digest(path) != sha:
            raise ValueError("input changed between independent child and observed phase")
    protocol, protocol_proof = _read(protocol_path, watched)
    dataset, dataset_proof = _read(dataset_path / "protocol.json", watched)
    results, results_proof = _read(results_path, watched)
    selected = gate["report"]["eligible_signal_ids"]
    capture = {"capture_proofs": [], "source_counts": None, "account_currency": None,
               "account_identity_sha256": None, "offset": None}
    try:
        contract = protocol.get("profile_payload", {}).get("capture_contract")
        if not isinstance(contract, dict):
            raise ValueError("frozen capture_contract missing")
        capture = _capture(manifests, dataset, dataset_path, watched, capture_contract=contract)
        evidence = _native_process(capture, selected, results,
                                   remaining=MAX_SECONDS - (time.monotonic() - started))
    except (ValueError, OSError, EOFError, KeyError, TypeError, OverflowError) as exc:
        evidence = _blocked_native(selected, f"native_preparation_blocked:{type(exc).__name__}:{exc}")
    report = {"contract": "simulator_forward_observed_comparison_v1",
              "forward_protocol_sha256": protocol_proof["sha256"],
              "dataset_sha256": dataset_proof["sha256"],
              "independent_results_sha256": results_proof["sha256"],
              "producer": {"path": str(ROOT / COMPARISON_SOURCES[0]),
                           "sha256": _digest(ROOT / COMPARISON_SOURCES[0])},
              "capture_proofs": capture["capture_proofs"], "cutoff_utc": dataset["cutoff_utc"],
              "cutoff_inclusive": True, "search_candidates": 0, "mass_search_authorized": False,
              "full_live_parity_verified": False, "quantitative_agreement_admitted": False,
              "approved_tolerances": None, "rows": evidence.pop("rows"),
              "denominator": len(selected), "excluded_signals": gate["report"]["excluded_signals"],
              "independent_validation": gate["report"], "input_proofs": [],
              "account_currency": capture["account_currency"],
              "account_identity_sha256": capture["account_identity_sha256"],
              "broker_epoch_offset_seconds": capture["offset"]}
    evidence["source_counts"] = capture["source_counts"]
    report["native_factual_review_status"] = (
        "complete" if report["rows"] and all(row["native_factual_review_status"] == "complete"
                                             for row in report["rows"]) else "incomplete")
    report["capability_gaps"] = sorted({gap for row in report["rows"] for gap in row["capability_gaps"]})
    report["coverage_gaps"] = sorted({gap for row in report["rows"] for gap in row["coverage_gaps"]})
    evidence_bytes = _encode(evidence)
    evidence_proof = {"path": str(out / "native_evidence.json"),
                      "sha256": hashlib.sha256(evidence_bytes).hexdigest()}
    for row in report["rows"]:
        for check in row["lifecycle_checks"].values():
            check["evidence"] = [evidence_proof, *capture["capture_proofs"]]
    report["input_proofs"] = [{"path": str(path), "sha256": sha} for path, sha in sorted(watched.items())]
    report["status"] = ("blocked" if any(row["blockers"] for row in report["rows"]) else
                        "incomplete" if report["coverage_gaps"] else "evidence_ready_for_review")
    encoded = _encode(report)
    if max(len(encoded), len(evidence_bytes)) > MAX_BYTES or time.monotonic() - started > MAX_SECONDS:
        raise ValueError("comparison output/time budget exceeded")
    for path, sha in watched.items():
        if _digest(path) != sha:
            raise ValueError("frozen source changed during native comparison")
    out.mkdir(parents=True, exist_ok=False)
    with (out / "native_evidence.json").open("xb") as stream:
        stream.write(evidence_bytes)
    with (out / "comparison.json").open("xb") as stream:
        stream.write(encoded)
    return {"status": report["status"], "comparison": str(out / "comparison.json"),
            "sha256": hashlib.sha256(encoded).hexdigest(), "denominator": len(selected),
            "blockers": sorted({reason for row in report["rows"] for reason in row["blockers"]}),
            "full_live_parity_verified": False, "quantitative_agreement_admitted": False,
            "mass_search_authorized": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--capture-manifest", type=Path, action="append", required=True,
                        help="Contiguous morning capture manifests, oldest first (maximum eight).")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = compare(args.protocol, args.dataset, args.results,
                         manifests=args.capture_manifest, out=args.out)
    except (ValueError, OSError, KeyError, TypeError, OverflowError, subprocess.TimeoutExpired) as exc:
        sys.stdout.write(json.dumps({"status": "blocked", "error": str(exc), "full_live_parity_verified": False}) + "\n")
        sys.stdout.flush()
        return 2
    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0 if result["status"] == "evidence_ready_for_review" else 2


if __name__ == "__main__":
    raise SystemExit(main())
