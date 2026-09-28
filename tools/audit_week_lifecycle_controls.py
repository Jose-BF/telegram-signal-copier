"""Compare frozen Telegram receipts with observed signal lifecycle controls.

This is an outcome-side validation inventory. Its events must never enter an
independent counterfactual replay.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.causal_replay import WEEKLY_ENTRY_MAX_AGE_S, utc
from research.causal_text_admission import candidate_text_entries


BROKER_OFFSET_HYPOTHESIS_S = 10_800


def audit(raw, lifecycle, native, anchor):
    if (raw.get("source_slice_sha256") != lifecycle.get("source_slice_sha256")
            or raw.get("start_offset") != lifecycle.get("start_offset")
            or raw.get("end_offset") != lifecycle.get("end_offset")
            or raw.get("start_utc") != lifecycle.get("start_utc")
            or raw.get("end_utc") != lifecycle.get("end_utc")):
        raise ValueError("raw and lifecycle controls do not bind to one source slice")
    if (lifecycle.get("event_count") != len(lifecycle.get("events", ()))
            or native.get("currency") != "EUR"
            or anchor.get("scope", {}).get("currency") != "EUR"):
        raise ValueError("lifecycle, native or currency input incomplete")
    received, closed, deferred = defaultdict(list), defaultdict(list), Counter()
    processing, text_only = defaultdict(list), defaultdict(list)
    for row in lifecycle["events"]:
        signal_id, event = row["sig"], row["ev"]
        stamp = utc(row["ts"])
        if event == "signal_received":
            received[signal_id].append(stamp)
            if row.get("trigger") == "text_only":
                text_only[signal_id].append(stamp)
        elif event == "signal_closed":
            closed[signal_id].append(stamp)
        elif event == "lifecycle_finalization_deferred":
            deferred[signal_id] += 1
        elif event == "canal1_text_processing" and type(row.get("source_msg_id")) is int:
            processing[row["source_msg_id"]].append((signal_id, stamp))
    if any(signal_id not in received or min(times) < min(received[signal_id])
           for signal_id, times in closed.items()):
        raise ValueError("signal closure precedes or lacks receipt within frozen slice")

    baskets = {row["signal_id"]: row for row in native["baskets"]}
    if len(baskets) != len(native["baskets"]):
        raise ValueError("duplicate native basket identity")
    clock_days = anchor.get("independent_clock_evidence", {}).get("days", {})
    signals = []
    for signal_id in sorted(received):
        first_receipt = min(received[signal_id])
        close_events = closed.get(signal_id, ())
        first_close = min(close_events) if close_events else None
        basket = baskets.get(signal_id)
        native_last, lag, clock_status = None, None, None
        if basket is not None:
            native_last = basket["last_native_calendar"]
            broker_clock = utc(native_last + "+00:00")
            native_utc = broker_clock - timedelta(seconds=BROKER_OFFSET_HYPOTHESIS_S)
            clock_status = clock_days.get(native_utc.date().isoformat(), {}).get("status", "missing")
            if first_close is not None:
                lag = round((first_close - native_utc).total_seconds(), 3)
        signals.append({
            "signal_id": signal_id,
            "first_received_at": first_receipt.isoformat(),
            "received_event_count": len(received[signal_id]),
            "first_signal_closed_at": first_close.isoformat() if first_close else None,
            "signal_closed_event_count": len(close_events),
            "deferred_event_count": deferred[signal_id],
            "native_basket_present": basket is not None,
            "native_last_broker_calendar": native_last,
            "native_to_signal_close_lag_s_hypothesis": lag,
            "clock_day_status": clock_status,
            "direct_clock_anchor_available": clock_status == "direct_anchor_available",
        })

    candidates, exclusions = candidate_text_entries(
        raw["rows"], start=utc(raw["start_utc"]), cutoff=utc(raw["end_utc"]),
        max_entry_age_s=WEEKLY_ENTRY_MAX_AGE_S)
    text_routes = []
    for candidate in candidates:
        message_id = int(candidate.signal_id.removeprefix("canal1_"))
        targets = processing.get(message_id, ())
        triggered = text_only.get(candidate.signal_id, ())
        if triggered and targets and all(signal_id == candidate.signal_id
                                         for signal_id, _ in targets):
            status, target = "text_only_received", candidate.signal_id
        elif not triggered and targets and len({signal_id for signal_id, _ in targets}) == 1:
            status, target = "processed_against_existing", targets[0][0]
        else:
            status, target = "unresolved_observed_route", None
        text_routes.append({
            "signal_id": candidate.signal_id,
            "observed_at": candidate.observed_at.isoformat(),
            "status": status,
            "target_signal_id": target,
            "processing_event_count": len(targets),
            "text_only_received_event_count": len(triggered),
        })
    route_counts = dict(sorted(Counter(row["status"] for row in text_routes).items()))
    return {
        "contract": "weekly_observed_lifecycle_control_audit_v1",
        "status": "diagnostic_only",
        "source_slice_sha256": raw["source_slice_sha256"],
        "received_signal_count": len(received),
        "closed_received_signal_count": sum(bool(closed.get(signal_id)) for signal_id in received),
        "received_without_close_in_slice": sorted(
            signal_id for signal_id in received if not closed.get(signal_id)),
        "deferred_signal_count": sum(bool(deferred[signal_id]) for signal_id in received),
        "deferred_event_count": sum(deferred.values()),
        "native_basket_count": len(baskets),
        "fresh_text_candidate_count": len(candidates),
        "stale_text_candidates": exclusions,
        "text_route_counts": route_counts,
        "text_routes": text_routes,
        "signals": signals,
        "broker_epoch_offset_seconds_hypothesis": BROKER_OFFSET_HYPOTHESIS_S,
        "observed_outcomes_not_replay_inputs": True,
        "full_simulator_parity_verified": False,
    }


def run(raw_path, lifecycle_path, native_path, anchor_path, output_path):
    paths = [Path(path) for path in (raw_path, lifecycle_path, native_path, anchor_path)]
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    inputs = [json.loads(path.read_text(encoding="utf-8-sig")) for path in paths]
    report = audit(*inputs)
    report["inputs_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in paths}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, ensure_ascii=True)
        stream.write("\n")
    return {"received": report["received_signal_count"],
            "closed": report["closed_received_signal_count"],
            "fresh_text_routes": report["text_route_counts"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("raw", "lifecycle", "native", "anchor", "output"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    sys.stdout.write(json.dumps(run(args.raw, args.lifecycle, args.native,
                                    args.anchor, args.output), sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
