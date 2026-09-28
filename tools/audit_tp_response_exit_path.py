"""Compare the first retained TP touch after MT5 acceptance with the native exit.

Offline diagnostic only: a quote touch and a TP-coded deal are distinct facts.
The response is not a precise server installation timestamp.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.audit_native_equity_snapshots import _tape
from tools.audit_native_money_anchor import digest, read
from tools.audit_tp_preack_touches import direct_same_day_offset


def post_response_touch(position, leg, times, bids, asks, *, offset_seconds):
    if (position["position_id"] != leg["native_position_id"]
            or position["signal_id"] != leg["signal_id"]
            or position["direction"] != leg["direction"]
            or position["entry_msc"] != leg["native_entry_source_msc"]
            or leg["broker_clock_offset_seconds"] != offset_seconds):
        raise ValueError("native position and TP leg identity mismatch")
    response_utc = leg["first_accepted_response_utc_msc"]
    response_source = response_utc + offset_seconds * 1000
    exit_source = position["exit_msc"]
    if (type(response_utc) is not int or type(exit_source) is not int
            or not position["entry_msc"] < response_source < exit_source
            or len(times) != len(bids) or len(times) != len(asks)):
        raise ValueError("invalid response or native exit chronology")
    target = Decimal(str(leg["target_level"]))
    if target != Decimal(str(position["exit_price"])):
        raise ValueError("native TP exit price differs from bound target")
    left = int(np.searchsorted(times, response_source, side="right"))
    right = int(np.searchsorted(times, exit_source, side="left"))
    quotes = bids if position["direction"] == "BUY" else asks
    touches = [(index, int(times[index]), Decimal(str(quotes[index]))) for index in range(left, right)
               if (Decimal(str(quotes[index])) >= target if position["direction"] == "BUY"
                   else Decimal(str(quotes[index])) <= target)]
    first = touches[0] if touches else None
    held = [Decimal(str(quotes[index])) for index in range(first[0], right)] if first else []
    unfavorable = sum((quote < target if position["direction"] == "BUY" else quote > target)
                      for quote in held)
    return {"signal_id": leg["signal_id"], "leg_index": leg["leg_index"],
            "native_position_id": position["position_id"], "direction": position["direction"],
            "target_level": str(target), "broker_clock_offset_seconds": offset_seconds,
            "first_accepted_response_utc_msc": response_utc,
            "native_tp_exit_utc_msc": exit_source - offset_seconds * 1000,
            "retained_tick_count_response_to_exit": right - left,
            "native_target_touch_count_response_to_exit": len(touches),
            "first_target_touch_utc_msc": first[1] - offset_seconds * 1000 if first else None,
            "first_target_touch_quote": str(first[2]) if first else None,
            "first_touch_to_native_exit_ms": exit_source - first[1] if first else None,
            "quote_min_first_touch_to_exit": str(min(held)) if held else None,
            "quote_max_first_touch_to_exit": str(max(held)) if held else None,
            "unfavorable_to_target_tick_count_after_first_touch": unfavorable,
            "status": "retained_touch_precedes_native_tp_exit" if first else "no_retained_touch_before_exit"}


def audit(preack_path, money_path, anchor_path, raw_dir):
    preack_path, money_path, anchor_path, raw_dir = map(
        Path, (preack_path, money_path, anchor_path, raw_dir))
    preack, money, anchor = map(read, (preack_path, money_path, anchor_path))
    if (preack.get("contract") != "tp_preack_retained_touch_diagnostic_v1"
            or money.get("contract") != "native_closed_money_anchor_v2"
            or anchor.get("contract") != "native_tick_anchor_diagnostic_v1"
            or preack.get("leg_count") != len(preack.get("rows", []))):
        raise ValueError("pre-ack, money or tick anchor contract mismatch")
    watched = {str(path): digest(path) for path in (preack_path, money_path, anchor_path, Path(__file__))}
    for name, expected in preack["inputs_sha256"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"pre-ack source changed: {name}")
    source_money = [sha for name, sha in preack["inputs_sha256"].items()
                    if Path(name).resolve() == money_path.resolve()]
    source_anchor = [sha for name, sha in preack["inputs_sha256"].items()
                     if Path(name).resolve() == anchor_path.resolve()]
    if source_money != [watched[str(money_path)]] or source_anchor != [watched[str(anchor_path)]]:
        raise ValueError("pre-ack report is not bound to native inputs")
    start = datetime.fromisoformat(anchor["scope"]["source_epoch_start"])
    end = datetime.fromisoformat(anchor["scope"]["source_epoch_end_exclusive"])
    times, bids, asks = _tape(raw_dir, anchor, "XAUUSD", start, end, watched)
    positions = {row["position_id"]: row for row in money["positions"]}
    if len(positions) != len(money["positions"]):
        raise ValueError("duplicate native position")
    rows = []
    seen = set()
    for leg in preack["rows"]:
        ticket = leg["native_position_id"]
        if ticket in seen or ticket not in positions:
            raise ValueError("duplicate or missing TP leg")
        seen.add(ticket)
        position = positions[ticket]
        offset = direct_same_day_offset(position, {
            "first_target_accepted_response_utc_msc": leg["first_accepted_response_utc_msc"]}, anchor)
        rows.append(post_response_touch(position, leg, times, bids, asks, offset_seconds=offset))
    if any(digest(Path(name)) != sha for name, sha in watched.items()):
        raise ValueError("TP exit sources changed during audit")
    lags = [row["first_touch_to_native_exit_ms"] for row in rows
            if row["first_touch_to_native_exit_ms"] is not None]
    return {"contract": "tp_response_to_native_exit_diagnostic_v1", "status": "diagnostic_only",
            "leg_count": len(rows), "legs_with_post_response_retained_touch": len(lags),
            "first_touch_to_exit_ms": {"min": min(lags) if lags else None,
                                       "max": max(lags) if lags else None,
                                       "at_most_20_ms_count": sum(value <= 20 for value in lags)},
            "rows": rows, "inputs_sha256": watched,
            "limitations": ["First quote touch is not a broker-side trigger or fill timestamp.",
                            "Successful response is not the exact server installation time.",
                            "The bounded cohort does not certify all signals or continuous equity."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preack", type=Path, required=True)
    parser.add_argument("--money", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.preack, args.money, args.anchor, args.raw_dir)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"legs": result["leg_count"], "post_response_touch_legs":
                      result["legs_with_post_response_retained_touch"]}), flush=True)


if __name__ == "__main__":
    main()
