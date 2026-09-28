"""Compare accepted client TP receipts with retained quotes before native exits.

This is an offline diagnostic. Retained terminal quotes are not broker-server
trigger evidence, and a client response does not date server installation.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.causal_replay import time_ns, utc
from tools.collect_vm_week_material import validate_segment_result
from tools.probe_canal1_incremental_window import digest, load_day


OFFSET_SECONDS = 10_800
MAX_EXITS = 32


def _require_hashes(report):
    for name, expected in report["inputs_sha256"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"source hash differs: {name}")


def _one(rows, label):
    if len(rows) != 1:
        raise ValueError(f"{label} is not unique")
    return rows[0]


def accepted_from_segment(segment, signal_id, position, model_at, target):
    """Bind one post-open TP snapshot inside a bounded archival segment."""
    start, end = utc(segment["segment"]["start_utc"]), utc(segment["segment"]["end_utc"])
    entry_at = datetime.fromtimestamp(
        position["entry_msc"] / 1000 - OFFSET_SECONDS, tz=start.tzinfo)
    if (signal_id not in segment["segment"]["active_signals"]
            or not start < entry_at < model_at < end):
        return None
    events = [row for row in segment["events"] if row.get("sig") == signal_id]
    result = _one([row for row in events if row.get("ev") == "mt5_order_result"
                   and row.get("deal") == position["deal_tickets"][0]], "native opening result")
    if (result.get("order") != position["position_id"] or result.get("retcode") != 10009
            or Decimal(str(result.get("price"))) != Decimal(str(position["entry_price"]))
            or Decimal(str(result.get("volume"))) != Decimal(str(position["volume"]))):
        raise ValueError("native opening result differs from position")
    initial = _one([row for row in events if row.get("ev") == "mt5_order_requested"
                    and row.get("action_id") == result.get("action_id")], "initial order request")
    if initial.get("tp") is not None:
        raise ValueError("initial TP acceptance unproven")
    accepted = []
    for snapshot in events:
        if (snapshot.get("ev") != "mt5_position_snapshot"
                or snapshot.get("after_action") != "MODIFY_SLTP"
                or snapshot.get("ticket") != position["position_id"]
                or snapshot.get("position_exists") is not True
                or snapshot.get("retcode") != 10009
                or utc(snapshot["ts"]) > model_at):
            continue
        if Decimal(str(snapshot.get("tp"))) != target:
            continue
        lineage = ("action_id", "attempt_id", "decision_id", "session_id", "ticket")
        matching = lambda kind: [row for row in events if row.get("ev") == kind
                                 and all(row.get(key) == snapshot.get(key) for key in lineage
                                         if key != "attempt_id" or kind != "mt5_modify_requested")]
        attempt = _one([row for row in matching("mt5_action_attempt")
                        if row.get("operation") == "MODIFY_SLTP"], "TP broker attempt")
        confirm = _one(matching("mt5_modify_confirmed"), "TP client confirmation")
        request = _one(matching("mt5_modify_requested"), "TP client request")
        if (attempt.get("broker_request_sent") is not True
                or attempt.get("result_retcode") != 10009
                or confirm.get("retcode") != 10009
                or any(Decimal(str(row.get(field))) != target for row, field in (
                    (request, "new_tp"), (attempt, "request_tp")))
                or not utc(request["ts"]) <= utc(attempt["broker_request_started_utc"])
                <= utc(attempt["broker_response_received_utc"]) <= utc(confirm["ts"])
                <= utc(snapshot["ts"]) <= model_at):
            raise ValueError("TP request-response-snapshot chain differs")
        accepted.append(snapshot)
    if not accepted:
        return None
    first = min(accepted, key=lambda row: utc(row["ts"]))
    for row in events:
        if (row.get("ev") == "mt5_action_attempt"
                and row.get("operation") == "MODIFY_SLTP"
                and row.get("ticket") == position["position_id"]
                and row.get("result_retcode") == 10009
                and utc(first["ts"]) < utc(row["broker_response_received_utc"]) <= model_at
                and Decimal(str(row.get("request_tp"))) != target):
            raise ValueError("later accepted TP change before modeled exit")
    return {"accepted_at": utc(first["ts"]),
            "snapshot_event_id": first["event_id"],
            "source": "bounded_archival_segment_post_accept_snapshot"}


def quote_corridor(times, bids, asks, flags, *, model_at, native_at, direction, target):
    if (len(times) != len(bids) or len(times) != len(asks) or len(times) != len(flags)
            or model_at >= native_at or direction not in {"BUY", "SELL"}):
        raise ValueError("invalid TP quote corridor")
    selected = (times >= time_ns(model_at)) & (times < time_ns(native_at))
    stamps = times[selected]
    side = (bids if direction == "BUY" else asks)[selected]
    hits = side >= float(target) if direction == "BUY" else side <= float(target)
    relevant = (flags[selected] & (2 if direction == "BUY" else 4)) != 0
    return {"status": "retained_ticks" if len(stamps) else "no_retained_ticks",
            "retained_tick_count": int(len(stamps)),
            "target_qualifying_tick_count": int(hits.sum()),
            "executable_side_update_tick_count": int(relevant.sum()),
            "target_qualifying_side_update_tick_count": int((hits & relevant).sum()),
            "all_retained_ticks_qualify": bool(len(stamps) and hits.all()),
            "first_tick_at": datetime.fromtimestamp(int(stamps[0]) / 1e9, tz=model_at.tzinfo).isoformat()
            if len(stamps) else None,
            "last_tick_at": datetime.fromtimestamp(int(stamps[-1]) / 1e9, tz=model_at.tzinfo).isoformat()
            if len(stamps) else None,
            "side_min": str(Decimal(str(float(side.min())))) if len(stamps) else None,
            "side_max": str(Decimal(str(float(side.max())))) if len(stamps) else None,
            "largest_retained_tick_gap_ms": int(np.diff(stamps).max() // 1_000_000)
            if len(stamps) > 1 else None}


def direct_day_anchor(anchor, day):
    evidence = anchor["independent_clock_evidence"]["days"].get(day, {})
    if (evidence.get("status") != "direct_anchor_available"
            or evidence.get("offset_seconds") != OFFSET_SECONDS
            or not evidence.get("anchors")):
        raise ValueError(f"direct source-clock anchor missing: {day}")
    return {"day": day, "status": evidence["status"],
            "offset_seconds": evidence["offset_seconds"],
            "anchor_count": len(evidence["anchors"])}


def audit(conditioned_path, money_path, timeline_path, segment_path, anchor_path, root):
    paths = tuple(map(Path, (conditioned_path, money_path, timeline_path,
                             segment_path, anchor_path)))
    conditioned, money, timeline, segment, anchor = [
        json.loads(path.read_text(encoding="utf-8")) for path in paths]
    if (conditioned.get("contract") != "weekly_native_entry_conditioned_risk_diagnostic_v1"
            or conditioned.get("status") != "diagnostic_only"
            or money.get("contract") != "native_closed_money_anchor_v2"
            or timeline.get("contract") != "tp_request_timeline_diagnostic_v1"
            or segment.get("contract") != "bounded_vm_week_material_segment_v2"
            or anchor.get("contract") != "native_tick_anchor_diagnostic_v1"
            or conditioned.get("input_basket_count") != len(conditioned.get("rows", []))
            or timeline.get("leg_count") != len(timeline.get("rows", []))):
        raise ValueError("TP corridor input contract differs")
    _require_hashes(conditioned)
    _require_hashes(timeline)
    for name, expected in anchor["input_sha256"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"clock anchor source hash differs: {name}")
    validate_segment_result(segment, segment["segment"])
    watched = {str(path): digest(path) for path in (*paths, Path(__file__),
               Path(__file__).with_name("probe_canal1_incremental_window.py"),
               Path(__file__).with_name("collect_vm_week_material.py"))}
    worker_hash = [expected for name, expected in segment["local_inputs_sha256"].items()
                   if Path(name).name == "probe_vm_signal_lifecycle.py"]
    if len(worker_hash) != 1:
        raise ValueError("archival material worker identity missing")
    current_worker = digest(Path(__file__).with_name("probe_vm_signal_lifecycle.py"))
    by_ticket = {row["native_position_id"]: row for row in timeline["rows"]}
    if len(by_ticket) != timeline["leg_count"]:
        raise ValueError("duplicate TP timeline ticket")
    positions = defaultdict(list)
    for position in money["positions"]:
        positions[position["signal_id"]].append(position)
    for values in positions.values():
        values.sort(key=lambda row: row["entry_msc"])
    tapes, clock_proofs = {}, []
    for proof in conditioned["tape_proofs"]:
        day = proof["day"]
        clock_proofs.append(direct_day_anchor(anchor, day))
        matching = [row for row in conditioned["rows"] if row["status"] == "mismatch"
                    and row["sequence"]["comparisons"][0]["simulated"]["at"][:10] == day]
        if not matching or day in tapes:
            raise ValueError("conditioned day or tape proof ambiguous")
        events = [item for row in matching for item in row["sequence"]["comparisons"]]
        start = min(utc(item["simulated"]["at"]) for item in events)
        end = max(utc(item["observed"]["at"]) for item in events)
        start_ns, cutoff_ns = time_ns(start), time_ns(end) + 1
        tape, source = load_day(Path(root), "XAUUSD", day,
                                start_ns=start_ns, cutoff_ns=cutoff_ns,
                                offset_seconds=OFFSET_SECONDS)
        if source["parquet_sha256"] != proof["market_sha256"]:
            raise ValueError("conditioned market tape differs")
        clock_proofs[-1]["tape_clock_admitted_by_source"] = source["clock_admitted_by_source"]
        frame = pd.read_parquet(Path(root) / "XAUUSD" / f"{day}.parquet",
                                columns=["time_msc", "flags"])
        source_times = (frame.time_msc.to_numpy(dtype=np.int64)
                        - OFFSET_SECONDS * 1000) * 1_000_000
        selected = (source_times >= start_ns) & (source_times < cutoff_ns)
        if not np.array_equal(source_times[selected], tape[0]):
            raise ValueError("TP flag tape differs from priced quotes")
        tapes[day] = (*tape, frame["flags"].to_numpy(dtype=np.uint32)[selected])
        for suffix in ("json", "parquet"):
            path = Path(root) / "XAUUSD" / f"{day}.{suffix}"
            watched[str(path)] = digest(path)
    expected_exits = sum(len(row.get("tp_receipt_states", [])) for row in conditioned["rows"]
                         if row["status"] == "mismatch")
    if not 1 <= expected_exits <= MAX_EXITS:
        raise ValueError("TP corridor exit budget or denominator invalid")
    rows = []
    for basket in conditioned["rows"]:
        if basket["status"] != "mismatch":
            continue
        signal = basket["signal_id"]
        exits = [row for row in basket["sequence"]["comparisons"] if row["kind"] == "exit"]
        states = {row["slot"]: row for row in basket["tp_receipt_states"]}
        if len(states) != len(exits):
            raise ValueError("conditioned receipt denominator differs")
        for event in exits:
            slot = event["slot"]
            position = positions[signal][slot - 1]
            state = states[slot]
            target = Decimal(str(event["simulated"]["price"]))
            model_at = utc(event["simulated"]["at"])
            native_at = utc(event["observed"]["at"])
            if (state["model_exit_at"] != model_at.isoformat()
                    or Decimal(str(position["exit_price"])) != target
                    or position["direction"] != event["simulated"]["direction"]):
                raise ValueError("TP corridor identity or target differs")
            receipt = by_ticket.get(position["position_id"])
            if receipt is not None:
                source = "bound_tp_timeline"
                accepted = state["status"].startswith("client_accepted_target_")
                accepted_at = utc(datetime.fromtimestamp(
                    receipt["first_target_accepted_response_utc_msc"] / 1000,
                    tz=model_at.tzinfo)) if accepted else None
            else:
                supplemental = accepted_from_segment(segment, signal, position, model_at, target)
                source = supplemental["source"] if supplemental else "missing_receipt_trace"
                accepted = supplemental is not None
                accepted_at = supplemental["accepted_at"] if supplemental else None
            if accepted and not accepted_at < model_at < native_at:
                raise ValueError("accepted TP response outside modeled/native exit interval")
            corridor = quote_corridor(*tapes[model_at.date().isoformat()],
                                      model_at=model_at, native_at=native_at,
                                      direction=position["direction"], target=target) if accepted else None
            rows.append({"signal_id": signal, "slot": slot,
                         "native_position_id": position["position_id"],
                         "receipt_status_at_model_exit": state["status"] if receipt else
                         "archival_accepted_before_model_exit" if accepted else "missing_receipt_trace",
                         "receipt_source": source,
                         "accepted_at": accepted_at.isoformat() if accepted_at else None,
                         "model_exit_at": model_at.isoformat(),
                         "native_exit_at": native_at.isoformat(),
                         "model_minus_native_ms": event["time_delta_ms"],
                         "target": str(target), "direction": position["direction"],
                         "corridor": corridor})
    if len(rows) != expected_exits:
        raise ValueError("TP corridor denominator changed")
    repeated_side_update_delays = [
        {"signal_id": row["signal_id"], "slot": row["slot"]}
        for row in rows
        if (row["corridor"] is not None
            and row["corridor"]["target_qualifying_side_update_tick_count"] >= 2)
    ]
    if any(digest(Path(name)) != expected for name, expected in watched.items()):
        raise ValueError("TP corridor source changed during audit")
    return {"contract": "conditioned_tp_quote_corridor_diagnostic_v1",
            "status": "diagnostic_only", "exit_count": len(rows),
            "accepted_before_model_exit_count": sum(row["corridor"] is not None for row in rows),
            "repeated_favorable_side_updates_before_native_exit": repeated_side_update_delays,
            "receipt_statuses": dict(sorted(Counter(row["receipt_status_at_model_exit"]
                                              for row in rows).items())),
            "rows": rows, "inputs_sha256": watched,
            "clock_proofs": clock_proofs,
            "archival_segment_worker_hash_matches_current": worker_hash[0] == current_worker,
            "full_historical_clock_admitted": False,
            "full_source_verification": False,
            "full_live_parity_verified": False,
            "limitations": [
                "A retained terminal tick is not a broker-server trigger or continuous quote path.",
                "Client TP acceptance and post-accept snapshot do not timestamp server installation.",
                "The archival segment worker no longer matches current source and its remote bytes were not reread.",
                "Native entries are fixed; hypothetical fills and account equity are not validated.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("conditioned", "money", "timeline", "segment", "anchor", "root", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.conditioned, args.money, args.timeline, args.segment,
                   args.anchor, args.root)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"exits": result["exit_count"],
                      "accepted": result["accepted_before_model_exit_count"],
                      "statuses": result["receipt_statuses"]}), flush=True)


if __name__ == "__main__":
    main()
