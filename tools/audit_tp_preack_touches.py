"""Find retained native TP touches before the first accepted MT5 response.

This is an offline timing diagnostic, not proof of when a TP was installed on
the broker. Only direct same-day clock anchors and source-bound ticks are used.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.audit_native_equity_snapshots import _tape
from tools.audit_native_money_anchor import digest, read


def preack_touch(position, leg, times, bids, asks, *, offset_seconds):
    if position["position_id"] != leg["native_position_id"] or position["signal_id"] != leg["signal_id"]:
        raise ValueError("native position and TP leg identity mismatch")
    direction = position["direction"]
    if direction not in ("BUY", "SELL") or type(offset_seconds) is not int:
        raise ValueError("invalid direction or broker clock offset")
    entry = position["entry_msc"]
    exit_at = position["exit_msc"]
    accepted_utc = leg["first_target_accepted_response_utc_msc"]
    end = accepted_utc + offset_seconds * 1000
    if (type(entry) is not int or type(exit_at) is not int or type(accepted_utc) is not int
            or not entry < end < exit_at or len(times) != len(bids) or len(times) != len(asks)):
        raise ValueError("invalid entry, response, exit or quote chronology")
    if Decimal(str(position["exit_price"])) != Decimal(str(leg["target_level"])):
        raise ValueError("native TP exit price differs from bound target")
    left = int(np.searchsorted(times, entry, side="left"))
    right = int(np.searchsorted(times, end, side="left"))
    if left == right:
        raise ValueError("no retained native ticks before accepted response")
    target = Decimal(str(leg["target_level"]))
    quotes = bids if direction == "BUY" else asks
    touches = [(int(times[index]), Decimal(str(quotes[index]))) for index in range(left, right)
               if (Decimal(str(quotes[index])) >= target if direction == "BUY"
                   else Decimal(str(quotes[index])) <= target)]
    first = touches[0] if touches else None
    first_utc = first[0] - offset_seconds * 1000 if first else None
    attempts = leg["attempts"]
    in_flight = [row for row in attempts
                 if first_utc is not None
                 and row["started_utc_msc"] <= first_utc < row["responded_utc_msc"]]
    return {"signal_id": leg["signal_id"], "leg_index": leg["leg_index"],
            "native_position_id": position["position_id"], "direction": direction,
            "target_level": str(target), "broker_clock_offset_seconds": offset_seconds,
            "native_entry_source_msc": entry, "first_accepted_response_utc_msc": accepted_utc,
            "retained_tick_count_before_response": right - left,
            "native_target_touch_count_before_response": len(touches),
            "first_target_touch_utc_msc": first_utc,
            "first_target_touch_quote": str(first[1]) if first else None,
            "first_target_touch_minus_virtual_close_tick_ms": (
                first_utc - leg["virtual_close_tick_utc_msc"] if first else None),
            "inflight_attempt_ids_at_first_touch": [row["attempt_id"] for row in in_flight],
            "inflight_later_rejected_at_first_touch_count": sum(
                row["retcode"] != 10009 for row in in_flight)}


def direct_same_day_offset(position, leg, anchor):
    entry = position["entry_msc"]
    exit_at = position["exit_msc"]
    day = datetime.fromtimestamp(entry / 1000, timezone.utc).date().isoformat()
    clock = anchor["independent_clock_evidence"]["days"].get(day, {})
    offset = clock.get("offset_seconds")
    if clock.get("status") != "direct_anchor_available" or type(offset) is not int:
        raise ValueError(f"native clock is not directly anchored: {day}")
    utc_days = {datetime.fromtimestamp(stamp / 1000, timezone.utc).date().isoformat()
                for stamp in (entry - offset * 1000, exit_at - offset * 1000,
                              leg["first_target_accepted_response_utc_msc"])}
    if utc_days != {day}:
        raise ValueError("TP interval crosses a clock-anchor day")
    return offset


def audit(timeline_path, money_path, anchor_path, raw_dir):
    timeline_path, money_path, anchor_path, raw_dir = map(
        Path, (timeline_path, money_path, anchor_path, raw_dir))
    timeline, money, anchor = map(read, (timeline_path, money_path, anchor_path))
    if (timeline.get("contract") != "tp_request_timeline_diagnostic_v1"
            or money.get("contract") != "native_closed_money_anchor_v2"
            or anchor.get("contract") != "native_tick_anchor_diagnostic_v1"
            or timeline.get("leg_count") != len(timeline.get("rows", []))):
        raise ValueError("TP, money or tick anchor contract mismatch")
    watched = {str(path): digest(path) for path in (timeline_path, money_path, anchor_path, Path(__file__))}
    for name, expected in timeline["inputs_sha256"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"TP timeline source changed: {name}")
    money_anchor = [sha for name, sha in money["inputs_sha256"].items()
                    if Path(name).resolve() == anchor_path.resolve()]
    if money_anchor != [watched[str(anchor_path)]]:
        raise ValueError("native money and tick anchor not bound")
    start = datetime.fromisoformat(anchor["scope"]["source_epoch_start"])
    end = datetime.fromisoformat(anchor["scope"]["source_epoch_end_exclusive"])
    times, bids, asks = _tape(raw_dir, anchor, "XAUUSD", start, end, watched)
    positions = {row["position_id"]: row for row in money["positions"]}
    if len(positions) != len(money["positions"]):
        raise ValueError("duplicate native position")
    rows = []
    seen = set()
    for leg in timeline["rows"]:
        ticket = leg["native_position_id"]
        if ticket in seen or ticket not in positions or leg["initial_order_requested_tp"] is not None:
            raise ValueError("TP leg identity or initial order differs")
        seen.add(ticket)
        position = positions[ticket]
        rows.append(preack_touch(position, leg, times, bids, asks,
                                 offset_seconds=direct_same_day_offset(position, leg, anchor)))
    if any(digest(Path(name)) != sha for name, sha in watched.items()):
        raise ValueError("TP touch sources changed during audit")
    return {"contract": "tp_preack_retained_touch_diagnostic_v1", "status": "diagnostic_only",
            "leg_count": len(rows), "legs_with_native_target_touch_before_accepted_response": sum(
                row["native_target_touch_count_before_response"] > 0 for row in rows),
            "rows": rows, "inputs_sha256": watched,
            "limitations": ["A retained tick touch is a market quote, not proof of a broker-side TP fill.",
                            "An accepted response does not timestamp exact server installation.",
                            "The bounded TP probe cannot rule out external modifications outside its window.",
                            "No continuous broker equity or full weekly journal coverage is established."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--money", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.timeline, args.money, args.anchor, args.raw_dir)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"legs": result["leg_count"], "preack_touch_legs":
                      result["legs_with_native_target_touch_before_accepted_response"]}), flush=True)


if __name__ == "__main__":
    main()
