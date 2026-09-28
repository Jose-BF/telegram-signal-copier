"""Offline identity crosswalk; never backdate a retained edited snapshot."""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import time

from research.causal_replay import compile_signals, raw_message, utc
from research.dubai_entry_probe import SCENARIOS, _archive, _rows
from research.strategy_study import (
    ROOT, _check_current, _digest, _encode, _invalid, _read, _sha, _unique,
    _utc_explicit, _verify_sources, _watch, current_identity,
)
from tools.audit_causal_lineage import _has_raw_message_evidence


SCHEMA = "dubai_raw_export_clock_audit_v1"
BUDGET = {"max_raw_bytes": 300_000_000, "max_raw_lines": 1_000_000,
          "max_retained_receipts": 50_000, "max_wall_seconds": 600}
EXTRA_SOURCES = ("research/dubai_clock_audit.py", "tools/audit_dubai_signal_clocks.py",
                 "research/dubai_entry_probe.py", "tools/audit_causal_lineage.py")
_IMPORTED = {name: _digest(ROOT / name) for name in EXTRA_SOURCES if (ROOT / name).is_file()}
ARTIFACTS = {"protocol.json", "originals.json", "raw_inventory.json", "crosswalk.json", "summary.json"}
LIMITATIONS = [
    "Raw receipts cover only the frozen original window, not the whole exported year.",
    "Same message and direction establish a paired clock experiment, not byte-identical media or all-content equivalence.",
    "Export edit markers do not prove that direction changed; final edited content is never moved to publication.",
    "Group-member comparisons include grouping semantics; they are not pure same-message clock changes.",
    "Missing, ambiguous, first-retained edits and conflicting identities remain in the denominator.",
    "No prices, strategy selection, observed money, orders, live changes or profitability conclusion in this audit.",
]


def _mid(value):
    if type(value) is not int or value <= 0:
        raise ValueError("message identity must be a positive integer")
    return value


def build_crosswalk(originals, triggers, decisions):
    """Match identities before consulting prices; keep both complete inventories."""
    originals, triggers, decisions = deepcopy((originals, triggers, decisions))
    raw_ids, message_ids, export = set(), set(), {}
    members, lookup = defaultdict(set), defaultdict(set)
    for row in originals:
        mid = _mid(row["message_id"])
        if row["signal_id"] != f"canal1_{mid}" or mid in message_ids or row["signal_id"] in raw_ids:
            raise ValueError("duplicate or mixed raw identity")
        if row["direction"] not in {"BUY", "SELL"} or type(row["retained_revision_is_edit"]) is not bool:
            raise ValueError("invalid raw direction or edit evidence")
        _utc_explicit(row["published_utc"])
        _utc_explicit(row["received_utc"])
        message_ids.add(mid)
        raw_ids.add(row["signal_id"])
    for row in triggers:
        mid, scenario, key = _mid(row["trigger_message_id"]), row["scenario"], row["trigger_id"]
        if (scenario not in SCENARIOS or type(row["chat_id"]) is not int or row["chat_id"] != 1642806869
                or key != f"dubai_stream:{scenario}:{mid}" or key in export):
            raise ValueError("duplicate or mixed export identity")
        if row["direction"] not in {"BUY", "SELL"} or row["clock_is_hypothesis"] is not True:
            raise ValueError("invalid export direction or clock contract")
        if _utc_explicit(row["trigger_utc"]) < _utc_explicit(row["published_utc"]):
            raise ValueError("export availability precedes publication")
        export[key] = row
        members[key].add(mid)
        lookup[(scenario, mid)].add(key)
    for row in decisions:
        scenario, mid, key = row["scenario"], _mid(row["message_id"]), row["trigger_id"]
        if scenario not in SCENARIOS:
            raise ValueError("unknown decision scenario")
        if key is None:
            continue
        if key not in export or export[key]["scenario"] != scenario:
            raise ValueError("decision references an unknown or mixed export trigger")
        members[key].add(mid)
        lookup[(scenario, mid)].add(key)
    mapped = {key: sorted(r["signal_id"] for r in originals if r["message_id"] in mids)
              for key, mids in members.items()}
    pairs = []
    for original in originals:
        for scenario in SCENARIOS:
            keys = sorted(lookup[(scenario, original["message_id"])])
            match = export[keys[0]] if len(keys) == 1 else None
            reasons, kind, member_ids = [], None, []
            receipt_delta = publication_delta = None
            if not keys:
                reasons.append("raw_message_has_no_export_trigger")
            elif len(keys) > 1:
                reasons.append("multiple_export_triggers_for_raw_message")
            else:
                key = keys[0]
                member_ids = sorted(members[key])
                direct = match["trigger_message_id"] == original["message_id"]
                kind = "direct_trigger" if direct else "group_member"
                if original["retained_revision_is_edit"]:
                    reasons.append("raw_original_revision_not_observed")
                if _utc_explicit(original["published_utc"]) > _utc_explicit(original["received_utc"]):
                    reasons.append("raw_publication_after_receipt")
                if original["direction"] != match["direction"]:
                    reasons.append("direction_differs")
                if direct and _utc_explicit(original["published_utc"]) != _utc_explicit(match["published_utc"]):
                    reasons.append("same_message_publication_differs")
                if len(mapped[key]) != 1:
                    reasons.append("multiple_raw_signals_share_export_trigger")
                stamp = _utc_explicit(match["trigger_utc"])
                receipt_delta = (stamp - _utc_explicit(original["received_utc"])).total_seconds()
                publication_delta = (stamp - _utc_explicit(original["published_utc"])).total_seconds()
            pairs.append({"raw_original": original, "export_trigger": match, "scenario": scenario,
                          "comparison_status": "not_comparable" if reasons else "paired_clock_control",
                          "reasons": reasons, "match_kind": kind, "export_member_message_ids": member_ids,
                          "possible_export_trigger_ids": keys,
                          "export_minus_receipt_seconds": receipt_delta,
                          "export_minus_publication_seconds": publication_delta})
    return {"pairs": pairs, "export_inventory": [
        {"export_trigger": r, "raw_signal_ids": mapped[r["trigger_id"]],
         "export_member_message_ids": sorted(members[r["trigger_id"]])} for r in triggers]}


def _signal_record(value):
    value = deepcopy(value)
    for name in ("observed_at", "published_at"):
        value[name] = utc(value[name]).isoformat()
    for event in value["provider_events"]:
        event["observed_at"] = utc(event["observed_at"]).isoformat()
    return json.loads(_encode(value))


def load_originals(raw_inputs, watch, deadline):
    directory = Path(raw_inputs).resolve()
    admission = _read(directory / "admission_summary.json")
    watch(directory / "admission_summary.json")
    watch(directory / "data_protocol.json", admission["data_protocol_sha256"])
    protocol = _read(directory / "data_protocol.json")
    if protocol["contract"] != "canal1_recursive_data_freeze_v2":
        raise ValueError("unsupported original raw corpus")
    watch(directory / "global_trigger_identity.json")
    historical = _read(directory / "global_trigger_identity.json")
    start = utc(protocol["development"]["from"] + "T00:00:00Z")
    end = utc(protocol["challenge"]["through"] + "T00:00:00Z") + timedelta(days=1)
    proof, retained = protocol["raw_source"], []
    source = Path(proof["path"]).resolve()
    if source.stat().st_size != proof["bytes"] or proof["bytes"] > BUDGET["max_raw_bytes"]:
        raise ValueError("raw source size mismatch or budget exceeded")
    watch(source, proof["sha256"])
    count = 0
    with source.open(encoding="utf-8") as stream:
        for count, line in enumerate(stream, 1):
            if count > BUDGET["max_raw_lines"]:
                raise ValueError("raw line budget exceeded")
            if count % 4096 == 0:
                deadline()
            row = json.loads(line, object_pairs_hook=_unique, parse_constant=_invalid)
            if row.get("ev") != "telegram_raw" or row.get("channel") != "canal1":
                continue
            if not start <= utc(row["ts"]) < end:
                continue
            if row.get("chat_id") != -1001642806869 or not _has_raw_message_evidence(row) or not row.get("message_revision_id"):
                raise ValueError("raw source chat or canonical evidence mismatch")
            retained.append((count, row))
            if len(retained) > BUDGET["max_retained_receipts"]:
                raise ValueError("raw receipt budget exceeded")
    signals, diagnostics = compile_signals([raw_message(r) for _, r in retained], start=start, cutoff=end,
                                           sticker_directions=protocol["sticker_directions"])
    actual = [_signal_record(asdict(s)) for s in signals]
    if actual != [_signal_record(r) for r in historical["signals"]] or len(actual) != historical["selected_window_trigger_count"]:
        raise ValueError("current raw trigger/provider compilation differs from frozen identities")
    if json.loads(_encode(diagnostics)) != historical["diagnostics"]:
        raise ValueError("current raw diagnostics differ from frozen inventory")
    first = {}
    for line, row in sorted(retained, key=lambda item: (utc(item[1]["ts"]), item[0])):
        first.setdefault(row["message_revision_id"], (line, row))
    originals = []
    for signal in actual:
        line, row = first[signal["message_revision_id"]]
        originals.append({"signal_id": signal["signal_id"], "message_id": row["message_id"],
            "direction": signal["direction"], "published_utc": signal["published_at"],
            "received_utc": signal["observed_at"], "chat_id": row["chat_id"],
            "retained_revision_is_edit": bool(row["is_edit"] or row["edit_date_utc"]),
            "message_revision_id": signal["message_revision_id"], "sticker_id": row["sticker_id"],
            "raw_media_sha256": row.get("media_sha256"), "source_line_1based": line,
            "source_row_sha256": _sha(row), "provider_events": signal["provider_events"]})
    mids = {r["message_id"] for r in originals}
    unresolved = []
    for revision, (line, row) in first.items():
        direction = protocol["sticker_directions"].get(str(row["sticker_id"]))
        if direction and row["message_id"] not in mids:
            unresolved.append({"message_id": row["message_id"], "message_revision_id": revision,
                "direction": direction, "received_utc": utc(row["ts"]).isoformat(),
                "published_utc": utc(row["date_utc"]).isoformat(), "source_line_1based": line,
                "source_row_sha256": _sha(row)})
    return originals, {"source_lines_scanned": count, "raw_receipts": len(retained),
        "unique_revisions": len(first), "compiled_signals": len(signals),
        "uncompiled_directional_revisions": unresolved, "compiler_diagnostics": list(diagnostics),
        "period": {"start_utc": start.isoformat(), "end_exclusive_utc": end.isoformat()}}


def summarize(crosswalk, raw_inventory):
    result = {"schema_version": SCHEMA, "raw_inventory": {k: v for k, v in raw_inventory.items()
              if k not in {"compiler_diagnostics", "uncompiled_directional_revisions"}},
              "raw_diagnostics": dict(Counter(r["reason"] for r in raw_inventory["compiler_diagnostics"])),
              "uncompiled_directional_revisions": len(raw_inventory["uncompiled_directional_revisions"]),
              "scenarios": {}, "strategy_evaluations": 0, "orders_sent": 0,
              "selected_policy": None, "limitations": LIMITATIONS}
    for scenario in SCENARIOS:
        rows = [r for r in crosswalk["pairs"] if r["scenario"] == scenario]
        paired = [r for r in rows if r["comparison_status"] == "paired_clock_control"]
        exports = [r for r in crosswalk["export_inventory"] if r["export_trigger"]["scenario"] == scenario]
        deltas = sorted(r["export_minus_receipt_seconds"] for r in paired)
        middle = len(deltas) // 2
        median = None if not deltas else deltas[middle] if len(deltas) % 2 else (deltas[middle-1] + deltas[middle]) / 2
        result["scenarios"][scenario] = {"raw_denominator": len(rows), "paired": len(paired),
            "paired_match_kinds": dict(Counter(r["match_kind"] for r in paired)),
            "unpaired_reasons": dict(Counter(reason for r in rows for reason in r["reasons"])),
            "annual_export_denominator": len(exports),
            "annual_export_without_raw_match": sum(not r["raw_signal_ids"] for r in exports),
            "export_minus_receipt_seconds": {"min": min(deltas, default=None), "median": median,
                "max": max(deltas, default=None), "earlier_than_receipt": sum(x < 0 for x in deltas),
                "later_by_over_60_seconds": sum(x > 60 for x in deltas),
                "later_by_over_300_seconds": sum(x > 300 for x in deltas)},
            "paired_export_edit_marked": sum(r["export_trigger"]["source_is_edit"] for r in paired)}
    return result


def _current(identity):
    _check_current(identity)
    if any(_digest(ROOT / name) != sha for name, sha in _IMPORTED.items()):
        raise ValueError("loaded audit code changed; use a fresh interpreter")


def _write(path, value):
    with Path(path).open("xb") as stream:
        stream.write(_encode(value))


def run_audit(raw_inputs, stream_dir, output_dir):
    started, watched = time.monotonic(), {}
    raw_inputs, stream_dir, output = (Path(p).resolve() for p in (raw_inputs, stream_dir, output_dir))
    if output.exists():
        raise ValueError("immutable output already exists")
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in (raw_inputs, stream_dir)):
        raise ValueError("output overlaps protected inputs")

    def deadline():
        if time.monotonic() - started >= BUDGET["max_wall_seconds"]:
            raise TimeoutError("clock audit budget exceeded")

    def watch(path, expected=None):
        deadline()
        path = Path(path).resolve()
        if path.is_relative_to(output):
            raise ValueError("output contains a protected source")
        _watch(path, _digest(path) if expected is None else expected, watched)

    identity = current_identity()
    _current(identity)
    for relative in EXTRA_SOURCES:
        watch(ROOT / relative)
    stream = _archive(stream_dir, "stream_identity_sha256", watch)
    if stream["schema_version"] != "dubai_entry_stream_v1":
        raise ValueError("unsupported exported entry stream")
    originals, inventory = load_originals(raw_inputs, watch, deadline)
    crosswalk = build_crosswalk(originals, _rows(stream_dir / "triggers.jsonl"), _rows(stream_dir / "decisions.jsonl"))
    summary = summarize(crosswalk, inventory)
    protocol = {"schema_version": SCHEMA, "implementation": identity, "budget": BUDGET,
        "raw_inputs": str(raw_inputs), "stream_dir": str(stream_dir),
        "stream_identity_sha256": stream["stream_identity_sha256"],
        "matching": "exact chat/message and frozen export complement membership; no outcome-dependent matching",
        "data_use": "retrospective_identity_diagnostic_only", "limitations": LIMITATIONS}
    _current(identity)
    _verify_sources(watched)
    deadline()
    output.mkdir(parents=True, exist_ok=False)
    payloads = {"protocol.json": protocol, "originals.json": originals, "raw_inventory.json": inventory,
                "crosswalk.json": crosswalk, "summary.json": summary}
    for name, value in payloads.items():
        _write(output / name, value)
    manifest = {"schema_version": SCHEMA, "status": "complete_identity_audit_only",
                "inputs": {"watched_files": watched},
                "artifacts": {name: {"sha256": _digest(output / name)} for name in payloads}}
    manifest["audit_identity_sha256"] = _sha(manifest)
    _write(output / "manifest.json", manifest)
    return summary


def verify_audit(output_dir):
    output, watched = Path(output_dir).resolve(), {}
    if {p.name for p in output.iterdir()} != ARTIFACTS | {"manifest.json"}:
        raise ValueError("incomplete or mixed audit archive")

    def watch(path, expected=None):
        _watch(path, _digest(path) if expected is None else expected, watched)

    manifest = _archive(output, "audit_identity_sha256", watch)
    if manifest["schema_version"] != SCHEMA or set(manifest["artifacts"]) != ARTIFACTS:
        raise ValueError("unsupported clock audit manifest")
    protocol = _read(output / "protocol.json")
    _current(protocol["implementation"])
    if protocol["budget"] != BUDGET or protocol["limitations"] != LIMITATIONS:
        raise ValueError("audit protocol changed")
    stream = Path(protocol["stream_dir"])
    originals, inventory = load_originals(protocol["raw_inputs"], watch, lambda: None)
    if originals != _read(output / "originals.json") or inventory != _read(output / "raw_inventory.json"):
        raise ValueError("retained originals differ from current frozen raw source")
    crosswalk = build_crosswalk(originals, _rows(stream / "triggers.jsonl"), _rows(stream / "decisions.jsonl"))
    if crosswalk != _read(output / "crosswalk.json") or summarize(crosswalk, inventory) != _read(output / "summary.json"):
        raise ValueError("crosswalk or summary recomputation differs")
    _current(protocol["implementation"])
    _verify_sources(watched)
    return {"status": "verified_identity_audit_only", "audit_identity_sha256": manifest["audit_identity_sha256"],
            "paired_counts": {s: sum(r["scenario"] == s and r["comparison_status"] == "paired_clock_control"
                                     for r in crosswalk["pairs"]) for s in SCENARIOS}}
