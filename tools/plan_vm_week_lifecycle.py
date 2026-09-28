"""Plan bounded VM journal windows for every native basket in the audit week."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path


OFFSET_MS = 10_800_000
SEGMENT = timedelta(minutes=5)
MAX_SEGMENTS = 800


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC required")
    return result


def native_at(time_msc):
    if type(time_msc) is not int:
        raise ValueError("native millisecond timestamp required")
    return datetime.fromtimestamp((time_msc - OFFSET_MS) / 1_000, timezone.utc)


def build_plan(calls, baskets, native, anchor):
    call_rows = {row["signal_id"]: row for row in calls["rows"]}
    basket_rows = {row["signal_id"]: row for row in baskets["baskets"]}
    if (len(call_rows) != len(calls["rows"]) or len(basket_rows) != len(baskets["baskets"])
            or set(call_rows) != set(basket_rows) or len(call_rows) != 40):
        raise ValueError("weekly native denominator must be 40 unique baskets")
    deals = {row["ticket"]: row for row in native["deals"]}
    if len(deals) != len(native["deals"]):
        raise ValueError("duplicate native deal ticket")
    clock_days = anchor["independent_clock_evidence"]["days"]
    rows = []
    segments = 0
    for signal in sorted(call_rows):
        call = call_rows[signal]
        owned_positions = [row for row in baskets["positions"] if row["signal_id"] == signal]
        tickets = [ticket for position in owned_positions for ticket in position["deal_tickets"]]
        if not owned_positions or not tickets or len(set(tickets)) != len(tickets):
            raise ValueError(f"native lifecycle tickets missing or duplicated: {signal}")
        owned_deals = [deals[ticket] for ticket in tickets]
        if any(deal["symbol"] != "XAUUSD" for deal in owned_deals):
            raise ValueError(f"non-XAUUSD native deal in basket: {signal}")
        native_times = [native_at(deal["time_msc"]) for deal in owned_deals]
        start = utc(call["receipt_utc"]) - timedelta(seconds=15)
        last = max(native_times)
        end = last + timedelta(seconds=60)
        if not start < min(native_times) <= last < end or end - start > timedelta(hours=6):
            raise ValueError(f"native lifecycle outside bounded receipt span: {signal}")
        days = sorted({at.date().isoformat() for at in native_times})
        statuses = {day: clock_days[day]["status"] for day in days}
        direct = all(status == "direct_anchor_available" for status in statuses.values())
        if direct != bool(call["direct_clock_anchor"]) and len(days) == 1:
            raise ValueError(f"first-call and lifecycle clock anchor disagree: {signal}")
        windows = []
        cursor = start
        while cursor < end:
            stop = min(cursor + SEGMENT, end)
            windows.append({"start_utc": cursor.isoformat(timespec="milliseconds"),
                            "end_utc": stop.isoformat(timespec="milliseconds")})
            cursor = stop
        segments += len(windows)
        if segments > MAX_SEGMENTS:
            raise ValueError("weekly lifecycle segment budget exceeded")
        rows.append({"signal_id": signal, "channel": basket_rows[signal]["channel"],
                     "clock_status": "direct_for_all_native_days" if direct else "offset_hypothesis",
                     "native_day_statuses": statuses, "receipt_utc": call["receipt_utc"],
                     "native_last_deal_utc_hypothesis": last.isoformat(timespec="milliseconds"),
                     "position_count": len(owned_positions), "deal_count": len(owned_deals),
                     "entry_deal_count": sum(deal["entry"] == 0 for deal in owned_deals),
                     "exit_deal_count": sum(deal["entry"] == 1 for deal in owned_deals),
                     "native_deal_tickets": tickets, "segments": windows})
    return {"contract": "week_native_lifecycle_window_plan_v1", "status": "plan_only",
            "native_basket_count": len(rows), "total_segments": segments,
            "clock_statuses": dict(Counter(row["clock_status"] for row in rows)),
            "position_count": sum(row["position_count"] for row in rows),
            "deal_count": sum(row["deal_count"] for row in rows), "rows": rows,
            "limitations": ["Broker-to-UTC subtraction is independently anchored only on direct days.",
                            "The final window ends 60 seconds after the last native deal; later journal terminalization is not proven absent.",
                            "Five-minute windows are a read plan, not extracted or validated lifecycle evidence."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=Path, required=True)
    parser.add_argument("--baskets", type=Path, required=True)
    parser.add_argument("--deals", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    paths = [args.calls, args.baskets, args.deals, args.anchor]
    calls, baskets, native, anchor = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    if (any(calls["inputs_sha256"].get(str(path)) != digest(path) for path in paths[1:])
            or baskets.get("source_sha256") != digest(args.deals)):
        raise ValueError("weekly lifecycle source hash mismatch")
    result = build_plan(calls, baskets, native, anchor)
    result["inputs_sha256"] = {str(path): digest(path) for path in [*paths, Path(__file__)]}
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2, ensure_ascii=True)
        target.write("\n")
    print(json.dumps({key: result[key] for key in
                      ("native_basket_count", "total_segments", "clock_statuses", "position_count", "deal_count")}))


if __name__ == "__main__":
    main()
