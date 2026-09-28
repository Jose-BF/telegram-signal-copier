"""Bounded native Gold TP/tick population audit; no fill or policy prediction."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import time

import numpy as np
import pandas as pd

from research.causal_replay import time_ns, utc
from tools.probe_canal1_incremental_window import digest, load_day
from tools.run_causal_controls import save


PROJECT = Path(__file__).resolve().parents[1]
OFFSET_SECONDS = 10_800
MAX_BASKETS = 20
MAX_POSITIONS = 100
MAX_DAYS = 3
MAX_SECONDS = 120
TP_COMMENT = re.compile(r"\[tp (\d+(?:\.\d+)?)\]")


def verify_bound_inputs(report):
    for name, expected in report["inputs_sha256"].items():
        path = Path(name)
        if digest(path if path.is_absolute() else PROJECT / path) != expected:
            raise ValueError(f"frozen input hash changed: {name}")


def target_from_deal(deal):
    if deal["reason"] != 5:
        return None
    match = TP_COMMENT.fullmatch(deal["comment"])
    if match is None:
        raise ValueError("TP deal lacks exact target comment")
    target = Decimal(match.group(1))
    if target <= 0 or Decimal(str(deal["price"])) != target:
        raise ValueError("TP fill price or target identity differs")
    return target


def terminal_touches(tape, flags, *, entry_ns, exit_ns, direction, target,
                     accepted_ns=None):
    """Describe retained executable-side updates, never server TP triggers."""
    times, bids, asks = tape
    if (len(times) != len(flags) or direction not in {"BUY", "SELL"}
            or entry_ns >= exit_ns or target <= 0):
        raise ValueError("invalid native TP corridor")
    first = int(np.searchsorted(times, entry_ns, side="left"))
    last = int(np.searchsorted(times, exit_ns, side="right"))
    stamps, selected_flags = times[first:last], flags[first:last]
    side = (bids if direction == "BUY" else asks)[first:last]
    favorable = side >= float(target) if direction == "BUY" else side <= float(target)
    side_updates = (selected_flags & (2 if direction == "BUY" else 4)) != 0
    touches = stamps[favorable & side_updates]
    confirmed = touches[touches >= accepted_ns] if accepted_ns is not None else np.array([], dtype=np.int64)
    return {"retained_tick_count": int(len(stamps)),
            "qualifying_tick_count": int(favorable.sum()),
            "qualifying_side_update_count": int(len(touches)),
            "first_qualifying_side_update_utc": (
                datetime.fromtimestamp(int(touches[0]) / 1e9, timezone.utc)
                if len(touches) else None),
            "first_touch_to_native_exit_ms": (
                int((exit_ns - touches[0]) // 1_000_000) if len(touches) else None),
            "last_touch_to_native_exit_ms": (
                int((exit_ns - touches[-1]) // 1_000_000) if len(touches) else None),
            "qualifying_side_updates_after_accepted_response": int(len(confirmed)),
            "first_post_accept_touch_to_native_exit_ms": (
                int((exit_ns - confirmed[0]) // 1_000_000) if len(confirmed) else None),
            "max_retained_tick_gap_ms": (
                int(np.diff(stamps).max() // 1_000_000) if len(stamps) > 1 else None)}


def accepted_target_response(trace, position, target, *, exit_ns):
    if trace is None:
        return None, "missing_client_tp_receipt_trace"
    if (trace["native_position_id"] != position["position_id"]
            or trace["signal_id"] != position["signal_id"]
            or Decimal(str(trace["target_level"])) != target):
        raise ValueError("TP receipt identity or target differs")
    if trace["initial_order_requested_tp"] is not None:
        return None, "initial_order_tp_acceptance_unproven"
    accepted = trace["first_target_accepted_response_utc_msc"]
    if accepted is None:
        return None, "target_accepted_response_missing"
    matching = [row for row in trace["attempts"]
                if row["retcode"] == 10009
                and Decimal(str(row["request_tp"])) == target
                and row["responded_utc_msc"] == accepted]
    if not matching:
        raise ValueError("first accepted TP response lacks attempt lineage")
    accepted_ns = accepted * 1_000_000
    if accepted_ns >= exit_ns:
        return None, "target_response_not_before_native_exit"
    later_changed = [row for row in trace["attempts"]
                     if row["retcode"] == 10009
                     and accepted < row["responded_utc_msc"] < exit_ns // 1_000_000
                     and Decimal(str(row["request_tp"])) != target]
    if later_changed:
        return None, "later_different_tp_accepted"
    return accepted_ns, "client_target_accepted_response_before_native_exit"


def audit(native_path, money_path, deals_path, timeline_path, root, output):
    started = time.monotonic()
    native_path, money_path, deals_path, timeline_path, root, output = (
        Path(path).resolve() for path in
        (native_path, money_path, deals_path, timeline_path, root, output))
    if output.exists():
        raise ValueError("immutable output already exists")
    native, money, deals, timeline = [
        json.loads(path.read_text(encoding="utf-8")) for path in
        (native_path, money_path, deals_path, timeline_path)]
    if (native["contract"] != "native_week_full_tick_risk_diagnostic_v1"
            or money["contract"] != "native_closed_money_anchor_v2"
            or timeline["contract"] != "tp_request_timeline_diagnostic_v1"
            or money["account_currency"] != "EUR"
            or native["source_clock_offset_seconds_hypothesis"] != OFFSET_SECONDS):
        raise ValueError("native TP population contract differs")
    verify_bound_inputs(native)
    verify_bound_inputs(timeline)
    if (native["inputs_sha256"].get(str(money_path.relative_to(PROJECT))) != digest(money_path)
            or native["inputs_sha256"].get(str(deals_path.relative_to(PROJECT))) != digest(deals_path)):
        raise ValueError("native money/deals not bound to risk path")
    baskets = [row for row in native["baskets"]
               if row["channel"] == "canal2"
               and row["direct_clock_anchor_for_all_event_days"] is True]
    basket_ids = {row["signal_id"] for row in baskets}
    if not baskets or len(baskets) > MAX_BASKETS or len(basket_ids) != len(baskets):
        raise ValueError("direct-clock Gold basket budget or identity invalid")
    positions = [row for row in money["positions"] if row["signal_id"] in basket_ids]
    if not positions or len(positions) > MAX_POSITIONS or len({row["position_id"] for row in positions}) != len(positions):
        raise ValueError("native position budget or identity invalid")
    expected_counts = Counter(row["signal_id"] for row in positions)
    if any(row["position_count"] != expected_counts[row["signal_id"]] for row in baskets):
        raise ValueError("native basket/position denominator differs")
    days = {row["source_epoch_days"][0] for row in baskets}
    if (len(days) > MAX_DAYS or any(len(row["source_epoch_days"]) != 1 for row in baskets)):
        raise ValueError("native TP day budget or span invalid")
    deals_by_position = {}
    for deal in deals["deals"]:
        if deal["entry"] == 1:
            deals_by_position.setdefault(deal["position_id"], []).append(deal)
    timeline_by_position = {row["native_position_id"]: row for row in timeline["rows"]}
    if len(timeline_by_position) != timeline["leg_count"]:
        raise ValueError("duplicate TP receipt position")
    tapes, tape_sources = {}, {}
    for day in sorted(days):
        start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        tape, proof = load_day(root, "XAUUSD", day,
                               start_ns=time_ns(start),
                               cutoff_ns=time_ns(start + timedelta(days=1)),
                               offset_seconds=OFFSET_SECONDS)
        frame = pd.read_parquet(root / "XAUUSD" / f"{day}.parquet",
                                columns=["time_msc", "flags"])
        stamps = (frame.time_msc.to_numpy(dtype=np.int64)
                  - OFFSET_SECONDS * 1000) * 1_000_000
        selected = ((stamps >= time_ns(start))
                    & (stamps < time_ns(start + timedelta(days=1))))
        if not np.array_equal(stamps[selected], tape[0]):
            raise ValueError("TP flags and priced tape differ")
        tapes[day] = (tape, frame["flags"].to_numpy(dtype=np.uint32)[selected])
        tape_sources[day] = proof
    rows = []
    for position in positions:
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError("native TP population wall budget exceeded")
        matches = deals_by_position.get(position["position_id"], [])
        if len(matches) != 1:
            raise ValueError("native position does not have one exit deal")
        deal = matches[0]
        if (deal["ticket"] not in position["deal_tickets"]
                or deal["time_msc"] != position["exit_msc"]
                or Decimal(str(deal["price"])) != Decimal(str(position["exit_price"]))
                or Decimal(str(deal["volume"])) != Decimal(str(position["volume"]))
                or deal["symbol"] != "XAUUSD"):
            raise ValueError("native TP deal/position identity differs")
        basket = next(row for row in baskets if row["signal_id"] == position["signal_id"])
        day = basket["source_epoch_days"][0]
        entry_ns = (position["entry_msc"] - OFFSET_SECONDS * 1000) * 1_000_000
        exit_ns = (position["exit_msc"] - OFFSET_SECONDS * 1000) * 1_000_000
        row = {"signal_id": position["signal_id"], "channel": "canal2",
               "position_id": position["position_id"], "exit_deal_ticket": deal["ticket"],
               "day": day, "direction": position["direction"],
               "native_entry_utc": datetime.fromtimestamp(entry_ns / 1e9, timezone.utc),
               "native_exit_utc": datetime.fromtimestamp(exit_ns / 1e9, timezone.utc),
               "native_exit_reason": deal["reason"],
               "native_net_eur": position["actual_net_eur"]}
        target = target_from_deal(deal)
        if target is None:
            row["status"] = "non_tp_exit"
        else:
            row["target"] = target
            accepted_ns, receipt_status = accepted_target_response(
                timeline_by_position.get(position["position_id"]),
                position, target, exit_ns=exit_ns)
            row["receipt_status"] = receipt_status
            row["accepted_response_utc"] = (
                datetime.fromtimestamp(accepted_ns / 1e9, timezone.utc)
                if accepted_ns is not None else None)
            tape, flags = tapes[day]
            corridor = terminal_touches(
                tape, flags, entry_ns=entry_ns, exit_ns=exit_ns,
                direction=position["direction"], target=target,
                accepted_ns=accepted_ns)
            row["terminal_corridor"] = corridor
            row["status"] = ("retained_tp_side_touch"
                             if corridor["qualifying_side_update_count"]
                             else "no_pre_exit_terminal_side_touch")
        rows.append(row)
    verify_bound_inputs(native)
    verify_bound_inputs(timeline)
    sources = {str(path): digest(path) for path in
               (native_path, money_path, deals_path, timeline_path,
                Path(__file__), PROJECT / "tools/probe_canal1_incremental_window.py")}
    report = {"contract": "native_gold_tp_terminal_touch_population_v1",
              "status": "diagnostic_only", "full_live_parity_verified": False,
              "broker_server_tp_trigger_verified": False,
              "baskets": len(baskets), "positions": len(positions),
              "statuses": dict(Counter(row["status"] for row in rows)),
              "receipt_statuses": dict(Counter(row.get("receipt_status", "not_tp")
                                               for row in rows)),
              "rows": rows, "sources": sources, "tapes": tape_sources,
              "limitations": ["Terminal Bid/Ask ticks are not broker server TP triggers.",
                              "First touch may precede TP installation where receipts are missing.",
                              "Native TP exit reason and comment establish observed target, not replay timing.",
                              "This is an all-eligible native population, not out-of-sample policy validation."],
              "elapsed_seconds": time.monotonic() - started}
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--money", type=Path, required=True)
    parser.add_argument("--deals", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.native, args.money, args.deals, args.timeline,
                   args.root, args.output)
    print({"output": str(args.output), "baskets": report["baskets"],
           "positions": report["positions"], "statuses": report["statuses"]})


if __name__ == "__main__":
    main()
