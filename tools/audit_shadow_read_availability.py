"""Measure when a recorded shadow transition could first use its tick.

Historical ticks without paired client read clocks remain unknown. This
diagnostic never treats an emitted shadow transition as a broker fill.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from tools.audit_native_money_anchor import digest
from tools.audit_week_shadow_control_path import controls_and_states, shadow_rows


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def utc_microseconds(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC timestamp required")
    delta = stamp - EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def classify_transition(row):
    result = {"signal_id": row["sig"], "candidate_id": row["candidate_id"],
              "event_id": row["event_id"], "transition": row["transition"]}
    tick = row.get("tick")
    if tick is None:
        return {**result, "status": "not_tick_driven"}
    source_msc = row.get("transition_tick_msc")
    if (type(source_msc) is not int or type(tick.get("time_msc")) is not int
            or source_msc != tick["time_msc"]):
        return {**result, "status": "blocked_tick_identity"}
    try:
        emitted_us = utc_microseconds(row["ts"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return {**result, "status": "blocked_emit_clock"}
    source_us = source_msc * 1_000
    if emitted_us < source_us:
        return {**result, "status": "blocked_emit_before_tick"}
    result.update(source_tick_msc=source_msc, emitted_at_utc=row["ts"],
                  source_to_emit_ms=(emitted_us - source_us) / 1_000)
    start = row.get("tick_batch_read_started_utc")
    completed = row.get("tick_batch_read_completed_utc")
    if start is None and completed is None:
        return {**result, "status": "missing_read_window"}
    if start is None or completed is None:
        return {**result, "status": "blocked_unpaired_read_window"}
    try:
        started_us = utc_microseconds(start)
        completed_us = utc_microseconds(completed)
    except (TypeError, ValueError, OverflowError):
        return {**result, "status": "blocked_read_clock"}
    if not started_us <= completed_us <= emitted_us or source_us > completed_us:
        return {**result, "status": "blocked_clock_order"}
    return {**result, "status": "read_window_observed",
            "read_started_at_utc": start, "read_completed_at_utc": completed,
            "source_to_read_complete_ms": (completed_us - source_us) / 1_000,
            "read_duration_ms": (completed_us - started_us) / 1_000,
            "read_complete_to_emit_ms": (emitted_us - completed_us) / 1_000}


def audit(slice_path, manifest_path):
    slice_path, manifest_path = Path(slice_path), Path(manifest_path)
    watched = {str(path): digest(path) for path in (
        slice_path, manifest_path, Path(__file__),
        Path(__file__).with_name("audit_week_shadow_control_path.py"))}
    source_rows = shadow_rows(slice_path, manifest_path)
    controls, states, registered, manifest_hash = controls_and_states(source_rows)
    rows = [classify_transition(event)
            for events in states.values() for event in events
            if event["ev"] == "strategy_shadow_transition"]
    bound_ids = {row["event_id"] for row in rows}
    for event in source_rows:
        if (event.get("ev") != "strategy_shadow_transition"
                or controls.get(event.get("channel")) != event.get("candidate_id")):
            continue
        if event.get("event_id") not in bound_ids:
            rows.append({"signal_id": event.get("sig"),
                         "candidate_id": event.get("candidate_id"),
                         "event_id": event.get("event_id"),
                         "transition": event.get("transition"),
                         "status": "blocked_registration_or_state_chain"})
    if len({row["event_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate shadow transition event")
    if any(digest(Path(name)) != sha for name, sha in watched.items()):
        raise ValueError("shadow availability input changed during audit")
    ticked = [row for row in rows if "source_to_emit_ms" in row]
    observed = [row for row in rows if row["status"] == "read_window_observed"]
    return {"contract": "shadow_tick_read_availability_diagnostic_v1",
            "status": "diagnostic_only", "control_manifest_hash": manifest_hash,
            "controls": controls, "registered_control_count": len(registered),
            "transition_count": len(rows), "ticked_with_emit_clock_count": len(ticked),
            "read_window_observed_count": len(observed),
            "source_to_emit_over_5s_count": sum(row["source_to_emit_ms"] > 5_000 for row in ticked),
            "source_to_emit_over_60s_count": sum(row["source_to_emit_ms"] > 60_000 for row in ticked),
            "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
            "rows": rows, "inputs_sha256": watched,
            "decision_availability_parity_verified": False,
            "broker_execution_parity_verified": False,
            "limitations": [
                "Without paired read clocks, emission only bounds when the shadow decision was recorded.",
                "A batch read window is not the receipt time of each tick or broker-server trigger time.",
                "A shadow transition is not an independently verified live order or fill.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slice", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit(args.slice, args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: report[key] for key in (
        "transition_count", "read_window_observed_count", "statuses")}), flush=True)


if __name__ == "__main__":
    main()
