"""Reconcile native MT5 deals by position and signal (generalized weekly_review_20260919).

Same identity and money rules as runtime_data/weekly_review_20260919/
summarize_native_week.py, with the calendar window and source as arguments.
Positions still open at capture time are listed separately, never netted.
Also emits the account's flat intervals (no bot position open), which bound
the replay windows that may assume an empty initial universe.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

IDENTITY = re.compile(r"(?:DCA_)?c([12])_(\d+)(?:_|$)")


def dec(value):
    return Decimal(str(value))


def native_calendar(msc):
    return datetime.fromtimestamp(msc / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def reconcile(payload, *, first_day, end_day):
    if payload["currency"] != "EUR":
        raise ValueError("EUR account expected")
    positions, cash, seen = defaultdict(list), [], set()
    for deal in payload["deals"]:
        if deal["ticket"] in seen:
            raise ValueError("duplicate deal")
        seen.add(deal["ticket"])
        if deal["type"] not in (0, 1):
            cash.append(deal)
            continue
        if deal["entry"] not in (0, 1):
            raise ValueError(f"unsupported deal entry kind {deal['ticket']}")
        positions[deal["position_id"]].append(deal)
    rows, still_open, signals = [], [], defaultdict(list)
    for position, deals in sorted(positions.items()):
        entries = [d for d in deals if d["entry"] == 0]
        exits = [d for d in deals if d["entry"] == 1]
        identities = set()
        for d in entries:
            match = IDENTITY.match(d["comment"])
            if not match:
                raise ValueError(f"unassigned opening {d['ticket']}")
            identities.add(f"canal{match[1]}_{match[2]}")
        if len(identities) != 1 or not entries:
            raise ValueError(f"position {position} has no single signal identity")
        signal = next(iter(identities))
        entry_volume = sum(dec(d["volume"]) for d in entries)
        exit_volume = sum(dec(d["volume"]) for d in exits)
        if not exits or exit_volume != entry_volume:
            still_open.append({"position_id": position, "signal_id": signal,
                               "entry_volume": str(entry_volume), "exit_volume": str(exit_volume)})
            continue
        net = sum((dec(d[f]) for d in deals for f in ("profit", "commission", "swap", "fee")), Decimal(0))
        row = {"position_id": position, "signal_id": signal,
               "symbol": entries[0]["symbol"],
               "net_eur": str(net.quantize(Decimal(".01"))),
               "entry_volume": str(entry_volume),
               "first_native_msc": min(d["time_msc"] for d in entries),
               "last_native_msc": max(d["time_msc"] for d in exits),
               "exit_reasons": sorted({d["reason"] for d in exits}),
               "deal_tickets": [d["ticket"] for d in deals]}
        rows.append(row)
        signals[signal].append(row)
    baskets = []
    for signal, group in sorted(signals.items()):
        first = min(r["first_native_msc"] for r in group)
        last = max(r["last_native_msc"] for r in group)
        if not (first_day <= native_calendar(first)[:10] < end_day):
            raise ValueError(f"{signal} opens outside the requested native calendar")
        baskets.append({"signal_id": signal, "channel": signal.split("_")[0], "positions": len(group),
                        "net_eur": str(sum(dec(r["net_eur"]) for r in group)),
                        "entry_volume": str(sum(dec(r["entry_volume"]) for r in group)),
                        "first_native_msc": first, "last_native_msc": last,
                        "first_native_calendar": native_calendar(first),
                        "last_native_calendar": native_calendar(last)})
    summary = []
    for channel in ("canal1", "canal2"):
        group = [b for b in baskets if b["channel"] == channel]
        summary.append({"channel": channel, "baskets": len(group),
                        "positions": sum(b["positions"] for b in group),
                        "net_eur": str(sum((dec(b["net_eur"]) for b in group), Decimal(0))),
                        "positive": sum(dec(b["net_eur"]) > 0 for b in group),
                        "negative": sum(dec(b["net_eur"]) < 0 for b in group)})
    # Flat intervals: native-clock spans with no reconciled position open.
    edges = sorted([(r["first_native_msc"], 1) for r in rows] + [(r["last_native_msc"], -1) for r in rows],
                   key=lambda item: (item[0], item[1]))
    busy, open_count, busy_start = [], 0, None
    for stamp, step in edges:
        if open_count == 0 and step == 1:
            busy_start = stamp
        open_count += step
        if open_count == 0 and step == -1:
            busy.append([busy_start, stamp])
    return {"contract": "native_week_reconciled_v2", "currency": "EUR",
            "source": "native MT5 deals, complete position volume and costs reconciled",
            "calendar": "native broker calendar; no UTC conversion claimed",
            "native_first_day": first_day, "native_end_day_exclusive": end_day,
            "summary": summary, "baskets": baskets, "positions": rows,
            "open_at_capture": still_open,
            "busy_native_intervals_msc": busy,
            "excluded_cash_movements": [{"ticket": d["ticket"], "profit": d["profit"], "comment": d["comment"]}
                                        for d in cash]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deals", type=Path, required=True)
    parser.add_argument("--first-day", required=True, help="native calendar YYYY-MM-DD")
    parser.add_argument("--end-day", required=True, help="native calendar YYYY-MM-DD, exclusive")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite")
    raw = args.deals.read_bytes()
    result = reconcile(json.loads(raw), first_day=args.first_day, end_day=args.end_day)
    result["source_sha256"] = hashlib.sha256(raw).hexdigest()
    result["source_path"] = args.deals.as_posix()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": result["summary"], "open_at_capture": len(result["open_at_capture"]),
                      "busy_intervals": len(result["busy_native_intervals_msc"])}, indent=1))


if __name__ == "__main__":
    main()
