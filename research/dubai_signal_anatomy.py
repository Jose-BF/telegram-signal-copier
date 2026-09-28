"""Offline signal/range anatomy. Observed quote paths, never fills or P/L."""

from collections import Counter
from pathlib import Path
import json
import platform
import time

import numpy as np

from parser import is_canal2_entry, parse_canal1_text
from provider_signal_catalog import SINGLE_ENTRY_RE, _deterministic_management_semantics
from research.causal_replay import utc
from research.dubai_annual_coverage import RawSource, iso_msc, msc
from research.dubai_clock_audit import _write
from research.dubai_receipt_inventory import classify_receipt
from research.strategy_study import (
    ROOT, _check_current, _digest, _invalid, _read, _sha, _unique,
    _verify_sources, _watch, current_identity,
)


SCHEMA = "dubai_signal_anatomy_v2"
HORIZONS = (300, 900, 3600, 14400)
BUDGET = {"max_signals": 300, "max_raw_lines": 1_000_000,
          "max_source_quotes": 150_000_000, "max_selected_quotes": 150_000_000,
          "max_wall_seconds": 3600}
SOURCES = ("research/dubai_signal_anatomy.py", "tools/analyze_dubai_signal_anatomy.py",
           "research/dubai_receipt_inventory.py", "research/dubai_annual_coverage.py",
           "parser.py", "provider_signal_catalog.py", "interpretation_firewall.py", "broker_tick_clock.py")
LIMITATIONS = [
    "Descriptive retrospective discovery, not a strategy, fills, P/L, portfolio or untouched validation.",
    "Only retained receipt identities June 5 to September 7; January-May exports are not original receipts.",
    "Implicit message links are hypotheses, not observed reply relationships. All revisions and exceptions remain visible.",
    "First priced NOW message is frozen at its receipt; single prices and ranges are separate. Later edits never rewrite it.",
    "Executable-side quotes include historical spread, but no commission, latency, slippage, size or protection mechanics.",
    "Price moves are XAUUSD price units, not pips, euros or dollars of account profit.",
    "Coverage means no retained quote gap above the declared threshold, not proof of broker history completeness.",
    "Partial paths show observed movement only; missing barriers are not evidence they were never reached.",
    "Quote order and range/TP/SL observations do not imply trade execution or provider management replication.",
    "FX and overnight money gates are inapplicable to price description and remain unchanged in the economic engines.",
    "A later recovery does not justify unlimited DCA; stops, interim loss, exposure and non-recovery still matter.",
]


def geometry(levels):
    direction, zone = levels.get("direction"), levels.get("range")
    if not zone and levels.get("entry_price") is not None:
        zone = [levels["entry_price"], levels["entry_price"]]
    targets, stop = levels.get("tps") or [], levels.get("sl")
    if direction not in {"BUY", "SELL"} or not zone or len(zone) != 2 or stop is None or not targets:
        return ["incomplete_operational_levels"]
    lo, hi = zone
    if not all(np.isfinite(float(v)) and float(v) > 0 for v in (*zone, *targets, stop)) or lo > hi:
        return ["invalid_level_values"]
    sign = 1 if direction == "BUY" else -1
    near, far = (hi, lo) if sign == 1 else (lo, hi)
    reasons = []
    if sign * (far - stop) <= 0:
        reasons.append("stop_not_beyond_far_edge")
    if any(sign * (target - near) <= 0 for target in targets):
        reasons.append("targets_not_beyond_near_edge")
    if any(sign * (b - a) <= 0 for a, b in zip(targets, targets[1:])):
        reasons.append("targets_not_ordered")
    return reasons


def barrier_reasons(levels):
    """Only fields needed for TP1/SL observations can block that description."""
    warnings = geometry(levels)
    if any(r in warnings for r in ("incomplete_operational_levels", "invalid_level_values")):
        return warnings
    zone = levels.get("range") or [levels["entry_price"], levels["entry_price"]]
    sign = 1 if levels["direction"] == "BUY" else -1
    far = zone[0] if sign == 1 else zone[1]
    reasons = [r for r in warnings if r == "stop_not_beyond_far_edge"]
    if sign * (levels["tps"][0] - far) <= 0:
        reasons.append("tp1_not_profitable_from_any_entry_in_zone")
    return reasons


def message_kind(text):
    levels = parse_canal1_text(text) or {}
    # This existing helper recognizes an immediate order, despite its channel-2 name.
    if is_canal2_entry(text):
        if levels.get("range"):
            return "operational_range", levels
        match = SINGLE_ENTRY_RE.search(text)
        if match:
            return "operational_price", {**levels, "entry_price": float(match.group(1))}
        return "immediate_notice", levels
    if levels.get("range"):
        return "range_commentary", levels
    management = _deterministic_management_semantics(text)
    if management:
        return "management", management
    return "other", {}


def associate(receipts, entries):
    """Keep availability and source proof; infer only from the preceding sticker."""
    roots = {r["message_id"]: {**r, "messages": []} for r in entries}
    seen, links, active, messages, rejected = set(), {}, None, [], []
    for line, row in sorted(receipts, key=lambda x: (utc(x[1]["ts"]), x[0])):
        mid, evidence = row["message_id"], classify_receipt(row)
        proof = {"source_line_1based": line, "source_row_sha256": _sha(row)}
        reasons = list(evidence["reasons"])
        if utc(row["date_utc"]) > utc(row["ts"]):
            reasons.append("publication_after_receipt")
        if row.get("edit_date_utc") and utc(row["edit_date_utc"]) > utc(row["ts"]):
            reasons.append("edit_after_receipt")
        if mid in roots and line == roots[mid]["first_source_line_1based"]:
            root = roots[mid]
            if proof["source_row_sha256"] != root["first_source_row_sha256"]:
                raise ValueError("inventory trigger differs from retained source")
            links[mid] = mid if root["initial_receipt_supported"] else None
            active = links[mid]
        if reasons:
            rejected.append({**proof, "message_id": mid, "reasons": reasons})
            continue
        key = (mid, row.get("text"), row.get("sticker_id"), row["date_utc"], row.get("edit_date_utc"))
        if key in seen:
            continue
        seen.add(key)
        if row.get("sticker_id") is not None:
            if mid not in roots:
                active, links[mid] = None, None
            continue
        if not row.get("text"):
            continue
        kind, parsed = message_kind(row["text"])
        reply = row.get("reply_to_msg_id")
        if mid in links:
            parent, method = links[mid], "same_message_revision"
        elif reply is not None:
            parent, method = links.get(reply), "explicit_reply" if links.get(reply) is not None else "unresolved_reply"
        else:
            parent, method = active, "preceding_sticker_inferred" if active is not None else "unlinked"
        if parent is not None and kind in {"operational_range", "operational_price", "immediate_notice"}:
            if parsed.get("direction") != roots[parent]["direction"]:
                parent, method = None, "direction_conflict"
        links[mid] = parent
        record = {**proof, "message_id": mid, "received_utc": utc(row["ts"]).isoformat(),
            "published_utc": row["date_utc"], "edit_date_utc": row.get("edit_date_utc"),
            "is_edit": bool(row.get("is_edit") or row.get("edit_date_utc")),
            "raw_chat_id": row.get("chat_id"), "raw_message_revision_id": row.get("message_revision_id"),
            "evidence_tier": evidence["tier"], "reply_to_msg_id": reply,
            "signal_id": roots[parent]["signal_id"] if parent is not None else None,
            "association": method, "kind": kind, "text": row["text"], "parsed": parsed,
            "geometry_reasons": geometry(parsed) if kind in {"operational_range", "operational_price"} else [],
            "barrier_reasons": barrier_reasons(parsed) if kind in {"operational_range", "operational_price"} else []}
        messages.append(record)
        if parent is not None:
            roots[parent]["messages"].append(record)
    for root in roots.values():
        all_priced = [m for m in root["messages"] if m["kind"] in {"operational_range", "operational_price"}]
        priced = [m for m in root["messages"] if m["kind"] == "operational_range"]
        root["first_priced"] = all_priced[0] if all_priced else None
        root["distinct_priced_messages"] = len({m["message_id"] for m in all_priced})
        root["first_range"] = priced[0] if priced else None
        root["distinct_range_messages"] = len({m["message_id"] for m in priced})
        root["range_revision_count"] = len(priced)
        root["first_consistent_revision_of_first_range"] = next((m for m in priced
            if m["message_id"] == priced[0]["message_id"] and not m["geometry_reasons"]), None) if priced else None
    return list(roots.values()), messages, rejected


def cut(tape, start_ms, end_ms):
    left = np.searchsorted(tape[0], start_ms * 1_000_000, side="left")
    right = np.searchsorted(tape[0], end_ms * 1_000_000, side="right")
    return tuple(a[left:right] for a in tape)


def completeness(times, start_ms, end_ms, max_gap_ms):
    if not len(times):
        return {"complete": False, "quotes": 0, "reasons": ["no_quotes"]}
    first, last = int(times[0] // 1_000_000 - start_ms), int(end_ms - times[-1] // 1_000_000)
    gaps = np.diff(times) / 1_000_000
    largest = float(gaps.max()) if len(gaps) else 0.0
    reasons = []
    if first > max_gap_ms:
        reasons.append("initial_gap")
    if last > max_gap_ms:
        reasons.append("final_gap")
    if largest > max_gap_ms:
        reasons.append("internal_gap")
    return {"complete": not reasons, "quotes": len(times), "reasons": reasons,
            "first_delay_ms": first, "last_age_ms": last, "max_internal_gap_ms": largest}


def first(mask):
    hits = np.flatnonzero(mask)
    return int(hits[0]) if len(hits) else None


def order(up, down):
    if up is None and down is None:
        return "neither_observed"
    if down is None or up is not None and up < down:
        return "favorable_first"
    if up == down:
        return "same_quote"
    return "adverse_first"


def point(times, index, start_ms):
    return None if index is None else {"utc": iso_msc(times[index] // 1_000_000),
        "seconds": float((times[index] // 1_000_000 - start_ms) / 1000), "ordinal": index}


def path_anatomy(tape, start_ms, end_ms, direction, max_gap_ms):
    if direction not in {"BUY", "SELL"} or end_ms <= start_ms:
        raise ValueError("invalid path direction or bounds")
    times, bid, ask = cut(tape, start_ms, end_ms)
    result = {"coverage": completeness(times, start_ms, end_ms, max_gap_ms)}
    if not len(times):
        return result
    sign = 1 if direction == "BUY" else -1
    reference = float(ask[0] if sign == 1 else bid[0])
    move = sign * ((bid if sign == 1 else ask) - reference)
    result.update(reference_quote_utc=iso_msc(times[0] // 1_000_000), reference_entry_side=reference,
        initial_spread=float(ask[0] - bid[0]), end_move=float(move[-1]),
        favorable_excursion=float(max(0, move.max())), adverse_excursion=float(max(0, -move.min())),
        barriers={}, recoveries={})
    for distance in (2, 5, 10):
        up, down = first(move >= distance), first(move <= -distance)
        result["barriers"][str(distance)] = {"order": order(up, down),
            "favorable": point(times, up, start_ms), "adverse": point(times, down, start_ms)}
    for distance in (2, 5):
        down = first(move <= -distance)
        recovered = first(move[down:] >= 0) if down is not None else None
        recovered = recovered + down if recovered is not None else None
        result["recoveries"][str(distance)] = {"retracement": point(times, down, start_ms),
            "recovery": point(times, recovered, start_ms),
            "worst_adverse_before_recovery_or_end": float(max(0, -move[down:recovered + 1
                if recovered is not None else None].min())) if down is not None else None}
    return result


def range_anatomy(tape, message, end_ms, max_gap_ms):
    result = {"range_message_id": message["message_id"], "availability_utc": message["received_utc"],
              "geometry_reasons": message["geometry_reasons"], "barrier_reasons": message["barrier_reasons"]}
    start = msc(message["received_utc"])
    if message["barrier_reasons"] or start >= end_ms:
        return dict(result, status="invalid_levels" if message["barrier_reasons"] else "range_after_cutoff")
    times, bid, ask = cut(tape, start, end_ms)
    result["coverage"] = completeness(times, start, end_ms, max_gap_ms)
    if not len(times):
        return dict(result, status="no_quotes")
    levels = message["parsed"]
    is_range = bool(levels.get("range"))
    lo, hi = levels["range"] if is_range else (levels["entry_price"], levels["entry_price"])
    sign = 1 if levels["direction"] == "BUY" else -1
    entry, exit_price = (ask, bid) if sign == 1 else (bid, ask)
    tp1, sl = levels["tps"][0], levels["sl"]
    zone = first((entry >= lo) & (entry <= hi)) if is_range else first(sign * (entry - lo) <= 0)
    tp, stop = first(sign * (exit_price - tp1) >= 0), first(sign * (exit_price - sl) <= 0)
    barrier = min([i for i in (tp, stop) if i is not None], default=len(times))
    location = "inside" if lo <= entry[0] <= hi else "favorable_outside" if sign * (entry[0] - (lo + hi) / 2) > 0 else "adverse_outside"
    result.update(status="observed", entry_type="range" if is_range else "single_price",
        opportunity_rule="quote_inside_published_range" if is_range else "quote_at_or_better_than_published_price",
        position_at_availability=location, initial_entry_side=float(entry[0]),
        width=float(hi - lo), tp1_from_mid=float(sign * (tp1 - (lo + hi) / 2)),
        stop_from_mid=float(sign * ((lo + hi) / 2 - sl)),
        first_zone=point(times, zone, start), first_tp1=point(times, tp, start), first_sl=point(times, stop, start),
        barrier_order=order(tp, stop), zone_before_barrier=zone is not None and zone < barrier)
    if zone is None or zone >= barrier:
        return result
    segment = entry[zone:barrier + 1]
    mid, far = (lo + hi) / 2, lo if sign == 1 else hi
    result["penetration"] = {}
    for label, level in (("midpoint", mid), ("far_edge", far)) if is_range else ():
        hit = first(sign * (segment - level) <= 0)
        absolute = zone + hit if hit is not None else None
        before = absolute is not None and absolute < barrier
        result["penetration"][label] = {"hit": point(times, absolute, start), "before_barrier": before,
            "already_at_first_zone_quote": absolute == zone if absolute is not None else False}
    zone_reference = float(entry[zone])
    movement = sign * (exit_price[zone:barrier + 1] - zone_reference)
    result["zone_excursion_until_barrier_or_end"] = {"reference_entry_side": zone_reference,
        "favorable": float(max(0, movement.max())), "adverse": float(max(0, -movement.min()))}
    return result


def distribution(values):
    values = [float(v) for v in values if v is not None]
    if not values:
        return {"n": 0}
    quantiles = np.quantile(values, [0, .1, .25, .5, .75, .9, 1])
    return {"n": len(values), "mean": float(np.mean(values)), **dict(zip(
        ("min", "p10", "p25", "median", "p75", "p90", "max"), map(float, quantiles)))}


def summarize(rows, messages, rejected):
    groups = {}
    for label, selected in [("all", rows)] + [(d, [r for r in rows if r["direction"] == d])
            for d in ("BUY", "SELL")] + [(m, [r for r in rows if r["received_utc"][:7] == m])
            for m in sorted({r["received_utc"][:7] for r in rows})]:
        group = {"identities": len(selected), "supported": sum(r["initial_receipt_supported"] for r in selected),
                 "paths": {}, "ranges": {}, "single_prices": {}}
        ranges = [r["first_range"] for r in selected if r["first_range"]]
        group["signals_with_operational_range"] = len(ranges)
        group["first_priced_types"] = dict(Counter(r["first_priced"]["kind"] if r["first_priced"] else "missing" for r in selected))
        group["multiple_priced_messages"] = sum(r["distinct_priced_messages"] > 1 for r in selected)
        group["first_priced_delay_seconds"] = distribution([(msc(r["first_priced"]["received_utc"]) - msc(r["received_utc"])) / 1000
            for r in selected if r["first_priced"]])
        group["consistent_first_range"] = sum(not m["geometry_reasons"] for m in ranges)
        group["multiple_priced_message_roots"] = sum(r["distinct_range_messages"] > 1 for r in selected)
        group["range_delay_seconds"] = distribution([(msc(r["first_range"]["received_utc"]) - msc(r["received_utc"])) / 1000
            for r in selected if r["first_range"]])
        for horizon in HORIZONS:
            paths = [r["paths"][str(horizon)] for r in selected if str(horizon) in r["paths"]]
            complete = [p for p in paths if p["coverage"]["complete"]]
            group["paths"][str(horizon)] = {"observed_windows": len(paths), "complete": len(complete),
                "partial_or_empty": len(paths) - len(complete),
                **{key: distribution([p.get(key) for p in complete]) for key in
                   ("end_move", "favorable_excursion", "adverse_excursion", "initial_spread")},
                "barrier_orders": {str(d): dict(Counter(p["barriers"][str(d)]["order"] for p in complete)) for d in (2, 5, 10)},
                "recoveries": {str(d): {"retraced": sum(p["recoveries"][str(d)]["retracement"] is not None for p in complete),
                    "recovered": sum(p["recoveries"][str(d)]["recovery"] is not None for p in complete),
                    "worst_adverse": distribution([p["recoveries"][str(d)]["worst_adverse_before_recovery_or_end"] for p in complete])}
                    for d in (2, 5)}}
            observations = [r["ranges"][str(horizon)] for r in selected if str(horizon) in r["ranges"]]
            valid = [r for r in observations if r.get("coverage", {}).get("complete")]
            touched = [r for r in valid if r["zone_before_barrier"]]
            group["ranges"][str(horizon)] = {"available": len(observations), "complete": len(valid),
                "statuses": dict(Counter(r["status"] for r in observations)),
                "position_at_availability": dict(Counter(r["position_at_availability"] for r in valid)),
                "zone_before_barrier": len(touched),
                "barrier_orders": dict(Counter(r["barrier_order"] for r in valid)),
                "orders_after_zone": dict(Counter(r["barrier_order"] for r in touched)),
                "penetration": {key: {outcome: sum(r["penetration"][key]["before_barrier"] and r["barrier_order"] == outcome for r in touched)
                    for outcome in ("favorable_first", "adverse_first", "neither_observed")} for key in ("midpoint", "far_edge")},
                "range_width": distribution([r["width"] for r in valid]),
                "tp1_from_mid": distribution([r["tp1_from_mid"] for r in valid]),
                "stop_from_mid": distribution([r["stop_from_mid"] for r in valid])}
            observations = [r["single_prices"][str(horizon)] for r in selected if str(horizon) in r["single_prices"]]
            valid = [r for r in observations if r.get("coverage", {}).get("complete")]
            reached = [r for r in valid if r["zone_before_barrier"]]
            group["single_prices"][str(horizon)] = {"available": len(observations), "complete": len(valid),
                "statuses": dict(Counter(r["status"] for r in observations)),
                "position_at_availability": dict(Counter(r["position_at_availability"] for r in valid)),
                "at_or_better_before_barrier": len(reached),
                "orders_after_opportunity": dict(Counter(r["barrier_order"] for r in reached)),
                "tp1_from_price": distribution([r["tp1_from_mid"] for r in valid]),
                "stop_from_price": distribution([r["stop_from_mid"] for r in valid])}
        groups[label] = group
    return {"schema_version": SCHEMA, "groups": groups,
        "message_versions": len(messages), "message_kinds": dict(Counter(m["kind"] for m in messages)),
        "operational_range_links": dict(Counter(m["association"] for m in messages if m["kind"] == "operational_range")),
        "unlinked_range_message_ids": sorted({m["message_id"] for m in messages if m["kind"] == "operational_range" and m["signal_id"] is None}),
        "rejected_receipts": len(rejected), "limitations": LIMITATIONS,
        "strategy_evaluations": 0, "orders_sent": 0, "selected_policy": None}


class BoundedSource(RawSource):
    def __init__(self, *args):
        super().__init__(*args)
        self.decoded_rows = 0

    def day(self, symbol, day):
        if (symbol, day) not in self.cache:
            self.decoded_rows += self.records[(symbol, day)]["rows"]
            if self.decoded_rows > BUDGET["max_source_quotes"]:
                raise ValueError("anatomy source quote budget exhausted")
        return super().day(symbol, day)


def run_anatomy(inventory_dir, coverage_protocol, raw_audit, output_dir, progress=lambda value: None):
    started, watched = time.monotonic(), {}
    inventory_dir, coverage_protocol, raw_audit, output = map(lambda p: Path(p).resolve(),
        (inventory_dir, coverage_protocol, raw_audit, output_dir))
    if output.exists():
        raise ValueError("immutable anatomy output already exists")

    def deadline():
        if time.monotonic() - started > BUDGET["max_wall_seconds"]:
            raise TimeoutError("anatomy wall budget exhausted")

    def watch(path, expected=None):
        deadline()
        path = Path(path).resolve()
        if path.is_relative_to(output) or output.is_relative_to(path):
            raise ValueError("output overlaps protected source")
        _watch(path, expected or _digest(path), watched)

    identity = current_identity()
    _check_current(identity)
    for name in SOURCES:
        watch(ROOT / name)
    watch(ROOT / "docs/development/2026-09-14-canal1-signal-anatomy-plan.md")
    manifest = _read(inventory_dir / "manifest.json")
    watch(inventory_dir / "manifest.json")
    key = "receipt_inventory_identity_sha256"
    if _sha({k: v for k, v in manifest.items() if k != key}) != manifest[key]:
        raise ValueError("inventory manifest mismatch")
    for name in ("protocol.json", "inventory.json"):
        watch(inventory_dir / name, manifest["artifacts"][name]["sha256"])
    inv_protocol = _read(inventory_dir / "protocol.json")
    entries = _read(inventory_dir / "inventory.json")["entries"]
    if not 0 < len(entries) <= BUDGET["max_signals"] or len({r["signal_id"] for r in entries}) != len(entries):
        raise ValueError("invalid anatomy universe")
    source_proof = inv_protocol["raw_source"]
    watch(source_proof["path"], source_proof["sha256"])
    receipts = []
    start, end = (utc(inv_protocol["period"][key]) for key in ("start_utc", "end_exclusive_utc"))
    with Path(source_proof["path"]).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if number > BUDGET["max_raw_lines"]:
                raise ValueError("anatomy raw line budget exhausted")
            if number % 4096 == 0:
                deadline()
            row = json.loads(line, object_pairs_hook=_unique, parse_constant=_invalid)
            if row.get("ev") == "telegram_raw" and row.get("channel") == "canal1" and start <= utc(row["ts"]) < end:
                receipts.append((number, row))
    available_lines = {line for line, _ in receipts}
    if any(r["first_source_line_1based"] not in available_lines for r in entries):
        raise ValueError("missing inventory first receipt")
    rows, messages, rejected = associate(receipts, entries)
    watch(coverage_protocol)
    protocol = _read(coverage_protocol)
    watch(raw_audit, protocol["raw_audit_sha256"])
    source = BoundedSource(raw_audit, protocol["broker_clock"]["segments"], watch)
    selected_quotes = 0
    for index, row in enumerate(rows):
        deadline()
        row.update(paths={}, ranges={}, single_prices={}, prior_context=None)
        if row["initial_receipt_supported"]:
            start_ms = msc(row["received_utc"])
            tape, days, missing = source.window("XAUUSD", start_ms - 900_000, start_ms + 14_400_000)
            tape = cut(tape, start_ms - 900_000, start_ms + 14_400_000)
            selected_quotes += len(tape[0])
            if selected_quotes > BUDGET["max_selected_quotes"]:
                raise ValueError("anatomy selected quote budget exhausted")
            row.update(source_days=days, missing_source_days=missing)
            before = cut(tape, start_ms - 900_000, start_ms - 1)
            row["prior_context"] = {"coverage": completeness(before[0], start_ms - 900_000, start_ms - 1, protocol["max_market_gap_ms"])}
            if len(before[0]):
                mid = (before[1] + before[2]) / 2
                row["prior_context"].update(directional_move=float((1 if row["direction"] == "BUY" else -1) * (mid[-1] - mid[0])),
                    price_range=float(mid.max() - mid.min()))
            for horizon in HORIZONS:
                end_ms = start_ms + horizon * 1000
                row["paths"][str(horizon)] = path_anatomy(tape, start_ms, end_ms, row["direction"], protocol["max_market_gap_ms"])
                if row["first_priced"]:
                    field = "ranges" if row["first_priced"]["kind"] == "operational_range" else "single_prices"
                    row[field][str(horizon)] = range_anatomy(tape, row["first_priced"], end_ms, protocol["max_market_gap_ms"])
        if (index + 1) % 30 == 0:
            progress({"signals": index + 1, "total": len(rows), "decoded_quotes": source.decoded_rows})
    summary = summarize(rows, messages, rejected)
    config = {"schema_version": SCHEMA, "implementation": identity, "budget": BUDGET,
        "inventory_identity": manifest[key], "period": inv_protocol["period"], "horizons_seconds": HORIZONS,
        "price_unit": "XAUUSD_price", "market_gap_ms": protocol["max_market_gap_ms"],
        "broker_clock": protocol["broker_clock"], "range_cutoff": "same horizon from initial sticker receipt",
        "discovery_plan_sha256": _digest(ROOT / "docs/development/2026-09-14-canal1-signal-anatomy-plan.md"),
        "python": platform.python_version(), "numpy": np.__version__, "limitations": LIMITATIONS}
    _check_current(identity)
    _verify_sources(watched)
    output.mkdir(parents=True)
    artifacts = {"protocol.json": config, "signals.json": rows, "messages.json": messages,
                 "rejected.json": rejected, "summary.json": summary}
    for name, value in artifacts.items():
        _write(output / name, value)
    result = {"schema_version": SCHEMA, "status": "complete_price_description_only",
        "artifacts": {name: {"sha256": _digest(output / name)} for name in artifacts},
        "inputs": {"watched_files": watched}, "resources": {"raw_lines": number,
        "decoded_source_quotes": source.decoded_rows, "selected_quotes": selected_quotes,
        "wall_seconds": time.monotonic() - started}}
    result["anatomy_identity_sha256"] = _sha(result)
    _write(output / "manifest.json", result)
    return {"identity": result["anatomy_identity_sha256"], "resources": result["resources"], "summary": summary}


def verify_anatomy(output_dir):
    output = Path(output_dir).resolve()
    manifest = _read(output / "manifest.json")
    key = "anatomy_identity_sha256"
    if _sha({k: v for k, v in manifest.items() if k != key}) != manifest[key]:
        raise ValueError("anatomy manifest mismatch")
    for name, proof in manifest["artifacts"].items():
        if Path(name).name != name or _digest(output / name) != proof["sha256"]:
            raise ValueError("anatomy artifact mismatch")
    _verify_sources(manifest["inputs"]["watched_files"])
    rows, messages, rejected = (_read(output / name) for name in ("signals.json", "messages.json", "rejected.json"))
    if summarize(rows, messages, rejected) != _read(output / "summary.json"):
        raise ValueError("anatomy summary mismatch")
    return {"identity": manifest[key], "status": "artifacts_sources_and_summary_verified",
            "quote_paths_recomputed": False, "strategy_evaluations": 0}
