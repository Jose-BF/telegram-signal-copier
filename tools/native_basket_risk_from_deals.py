"""Per-basket floating/drawdown path of the REAL fills, marked on every retained tick.

Same computation as tools/audit_native_week_risk_path.py (imports its
native_events / quote_grid / summarize_path and research.risk_trajectory),
but fed directly from reconciled native deals and a verified tick root, so a
new week does not need the journal-receipt anchor chain first. The UTC offset
stays the 10,800 s hypothesis used by the rest of the chain. Regression: it
must reproduce native_week_risk_path_v1.json for 14-18/09 exactly.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.risk_trajectory import RiskSpec, reconstruct_risk  # noqa: E402
from tools.audit_native_money_anchor import cents  # noqa: E402
from tools.audit_native_week_risk_path import (  # noqa: E402
    MARKET_MAX_AGE_MS, OFFSET_SECONDS, native_events, quote_grid, summarize_path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_tape(root, symbol, first_day, end_day, watched):
    frames, day = [], datetime.fromisoformat(first_day)
    end = datetime.fromisoformat(end_day)
    while day < end:
        label = day.date().isoformat()
        meta_path = root / symbol / f"{label}.json"
        data_path = meta_path.with_suffix(".parquet")
        if not meta_path.exists():
            raise ValueError(f"missing {symbol} {label}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("status") != "raw_reads_consistent" or meta.get("sha256") != digest(data_path):
            raise ValueError(f"unverified raw price day {symbol} {label}")
        frame = pd.read_parquet(data_path, columns=["time_msc", "bid", "ask"])
        if len(frame) != meta["rows"]:
            raise ValueError("price row count differs from raw metadata")
        watched[meta_path.as_posix()] = digest(meta_path)
        watched[data_path.as_posix()] = meta["sha256"]
        frames.append(frame)
        day += timedelta(days=1)
    joined = pd.concat(frames, ignore_index=True)
    times = joined.time_msc.to_numpy(dtype=np.int64)
    bid, ask = joined.bid.to_numpy(dtype=float), joined.ask.to_numpy(dtype=float)
    if (np.any(np.diff(times) < 0) or not np.isfinite(bid).all() or not np.isfinite(ask).all()
            or np.any(bid <= 0) or np.any(ask < bid)):
        raise ValueError("invalid price tape")
    return times, bid, ask


def money_positions(deals, reconciled):
    by_ticket = {deal["ticket"]: deal for deal in deals}
    rows = []
    for position in reconciled["positions"]:
        own = [by_ticket[t] for t in position["deal_tickets"]]
        entry = [d for d in own if d["entry"] == 0]
        exits = [d for d in own if d["entry"] == 1]
        net = sum((cents(d[k]) for d in own for k in ("profit", "commission", "swap", "fee")), Decimal(0))
        if net != cents(position["net_eur"]) or not entry or not exits:
            raise ValueError(f"position money or shape differs: {position['position_id']}")
        rows.append({"position_id": position["position_id"], "signal_id": position["signal_id"],
                     "deal_tickets": position["deal_tickets"], "actual_net_eur": str(net),
                     "entry_msc": min(d["time_msc"] for d in entry),
                     "exit_msc": max(d["time_msc"] for d in exits)})
    return rows


def audit(tick_root, deals_path, reconciled_path, contract_path, first_day, end_day):
    started = time.monotonic()
    tick_root, deals_path, reconciled_path, contract_path = map(
        Path, (tick_root, deals_path, reconciled_path, contract_path))
    native = json.loads(deals_path.read_text(encoding="utf-8"))
    reconciled = json.loads(reconciled_path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if reconciled.get("source_sha256") != digest(deals_path):
        raise ValueError("reconciled baskets not derived from these deals")
    watched = {p.as_posix(): digest(p) for p in (deals_path, reconciled_path, contract_path, Path(__file__))}
    market = load_tape(tick_root, "XAUUSD", first_day, end_day, watched)
    conversion = load_tape(tick_root, "EURUSD", first_day, end_day, watched)
    spec = RiskSpec(native["currency"], contract["account"]["currency_digits"],
                    Decimal(str(contract["instrument"]["contract_size"])),
                    contract["conversion"]["orientation"],
                    contract["conversion"]["max_quote_age_ms"], MARKET_MAX_AGE_MS)
    if spec.currency != "EUR" or spec.orientation != "account_base_profit_quote":
        raise ValueError("unexpected account conversion contract")
    positions = money_positions(native["deals"], reconciled)
    by_signal = defaultdict(list)
    for position in positions:
        by_signal[position["signal_id"]].append(position)
    all_events = native_events(native["deals"], positions)
    reports = []
    for basket in reconciled["baskets"]:
        signal = basket["signal_id"]
        group = by_signal[signal]
        row = {"signal_id": signal, "channel": basket["channel"],
               "position_count": len(group), "native_net_eur": basket["net_eur"]}
        try:
            events = [event for position in group for event in all_events[position["position_id"]]]
            if sum((event.money for event in events), Decimal(0)) != cents(basket["net_eur"]):
                raise ValueError("basket native money differs from event sequence")
            row["source_epoch_days"] = sorted({
                datetime.fromtimestamp(int(v) / 1000, timezone.utc).date().isoformat()
                for position in group for v in (position["entry_msc"], position["exit_msc"])})
            reconstructed = reconstruct_risk(
                events, quote_grid(market, conversion, events), spec=spec,
                retrospective_fx_interval_ms=contract["conversion"]["max_quote_interval_ms"])
            row["path"] = summarize_path(reconstructed)
            row["status"] = "retrospective_complete" if not reconstructed["blockers"] else "blocked"
            if (reconstructed["metrics"] is not None
                    and reconstructed["metrics"]["final_net"] != cents(basket["net_eur"])):
                raise ValueError("risk final net differs from reconciled native basket")
        except (ValueError, KeyError) as exc:
            row.update(status="blocked", blocker=str(exc))
        reports.append(row)
    for path, sha in watched.items():
        if digest(path) != sha:
            raise ValueError("risk source changed during reconstruction")
    return {"contract": "native_basket_risk_from_deals_v1", "status": "diagnostic_only",
            "equivalent_to": "native_week_full_tick_risk_diagnostic_v1 path metrics",
            "source_clock_offset_seconds_hypothesis": OFFSET_SECONDS, "clock_admitted": False,
            "native_first_day": first_day, "native_end_day_exclusive": end_day,
            "basket_count": len(reports), "statuses": dict(Counter(r["status"] for r in reports)),
            "baskets": reports, "inputs_sha256": watched,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "limitations": ["Every retained XAU tick is marked; unobserved between-tick extrema remain unknown.",
                            "FX gaps over the contract interval are bracketed retrospectively.",
                            "Broker epoch to UTC remains the +10,800 s hypothesis.",
                            "Per-basket risk excludes shared-account equity, credit, margin and other positions."]}


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tick-root", type=Path, required=True)
    parser.add_argument("--deals", type=Path, required=True)
    parser.add_argument("--reconciled", type=Path, required=True)
    parser.add_argument("--broker-contract", type=Path, required=True)
    parser.add_argument("--first-day", required=True)
    parser.add_argument("--end-day", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite")
    report = audit(args.tick_root, args.deals, args.reconciled, args.broker_contract,
                   args.first_day, args.end_day)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(_jsonable(report), stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"statuses": report["statuses"], "elapsed_seconds": report["elapsed_seconds"]}))


if __name__ == "__main__":
    main()
