"""Compare retained account equity snapshots with causal native-deal marks."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from broker_money import convert_profit_amount
from tools.audit_native_money_anchor import cents, digest, read


MAX_EVENTS = 10_000
MAX_TICKS = 5_000_000
MARKET_MAX_AGE_MS = 5_000


def snapshot_row(event, positions, market, conversion, *, offset_seconds, clock_status,
                 max_fx_age_ms, contract_size):
    stamp = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError("account event requires explicit UTC")
    source_msc = int(stamp.timestamp() * 1000) + offset_seconds * 1000
    actual = cents(event["equity"]) - cents(event["balance"])
    open_rows = [row for row in positions if row["entry_msc"] <= source_msc < row["exit_msc"]]
    row = {"event_id": event["event_id"], "event_utc": event["ts"],
           "source_epoch_msc_hypothesis": source_msc, "clock_status": clock_status,
           "equity_minus_balance_eur": str(actual), "open_position_ids": [p["position_id"] for p in open_rows],
           "open_signal_ids": sorted({p["signal_id"] for p in open_rows}),
           "open_position_count": len(open_rows)}
    if not open_rows:
        row["status"] = ("flat_outside_price_window" if clock_status == "outside_extracted_window" else "flat_at_event_time") if actual == 0 else "unattributed_account_floating"
        row["modeled_floating_eur"] = "0.00"
        row["model_minus_account_eur"] = str(-actual)
        return row
    market_times, bid, ask = market
    fx_times, fx_bid, fx_ask = conversion
    market_index = int(np.searchsorted(market_times, source_msc, side="right")) - 1
    fx_index = int(np.searchsorted(fx_times, source_msc, side="right")) - 1
    if market_index < 0 or fx_index < 0:
        row["status"] = "blocked_missing_prior_quote"
        return row
    market_age = source_msc - int(market_times[market_index])
    fx_age = source_msc - int(fx_times[fx_index])
    row.update(market_tick_msc=int(market_times[market_index]), market_age_ms=market_age,
               fx_tick_msc=int(fx_times[fx_index]), fx_age_ms=fx_age)
    if market_age > MARKET_MAX_AGE_MS or fx_age > max_fx_age_ms:
        row["status"] = "blocked_stale_quote"
        return row
    values = []
    for position in open_rows:
        direction = position["direction"]
        if direction not in ("BUY", "SELL"):
            raise ValueError("invalid native position direction")
        side = bid[market_index] if direction == "BUY" else ask[market_index]
        raw = ((Decimal(str(side)) - Decimal(str(position["entry_price"])))
               * (1 if direction == "BUY" else -1)
               * Decimal(str(position["volume"])) * Decimal(str(contract_size)))
        rate = Decimal(str(fx_ask[fx_index] if raw >= 0 else fx_bid[fx_index]))
        value = convert_profit_amount(raw, rate, orientation="account_base_profit_quote", currency_digits=2)
        values.append({"position_id": position["position_id"], "modeled_eur": str(value)})
    modeled = sum((Decimal(item["modeled_eur"]) for item in values), Decimal(0))
    row.update(position_marks=values, modeled_floating_eur=str(modeled),
               model_minus_account_eur=str(modeled - actual),
               status="nonzero_snapshot_exact" if modeled == actual else "nonzero_snapshot_difference")
    return row


def _tape(raw_dir, anchor, symbol, start, end, watched):
    frames = []
    day = start
    while day < end:
        label = day.date().isoformat()
        meta_path = raw_dir / symbol / f"{label}.json"
        data_path = meta_path.with_suffix(".parquet")
        meta = read(meta_path)
        for path in (meta_path, data_path):
            anchored = [sha for name, sha in anchor["input_sha256"].items()
                        if Path(name).resolve() == path.resolve()]
            if len(anchored) != 1 or digest(path) != anchored[0]:
                raise ValueError(f"price tape not bound to native anchor: {path}")
            watched[str(path)] = anchored[0]
        if meta["status"] != "raw_reads_consistent" or meta["sha256"] != digest(data_path):
            raise ValueError("unverified raw price day")
        frame = pd.read_parquet(data_path, columns=["time_msc", "bid", "ask"])
        if len(frame) != meta["rows"]:
            raise ValueError("price row count differs from raw metadata")
        frames.append(frame)
        day += timedelta(days=1)
    joined = pd.concat(frames, ignore_index=True)
    if len(joined) > MAX_TICKS:
        raise ValueError("account snapshot quote budget exceeded")
    times = joined.time_msc.to_numpy(dtype=np.int64)
    bid, ask = joined.bid.to_numpy(dtype=float), joined.ask.to_numpy(dtype=float)
    if (np.any(np.diff(times) < 0) or not np.isfinite(bid).all() or not np.isfinite(ask).all()
            or np.any(bid <= 0) or np.any(ask < bid)):
        raise ValueError("invalid price tape")
    return times, bid, ask


def audit(raw_dir, native_money_path, native_anchor_path, broker_contract_path,
          shadow_slice_path, shadow_manifest_path):
    raw_dir, native_money_path, native_anchor_path, broker_contract_path, shadow_slice_path, shadow_manifest_path = map(
        Path, (raw_dir, native_money_path, native_anchor_path, broker_contract_path,
               shadow_slice_path, shadow_manifest_path))
    money, anchor, contract, manifest = map(read, (native_money_path, native_anchor_path,
                                                   broker_contract_path, shadow_manifest_path))
    if (money.get("contract") != "native_closed_money_anchor_v1"
            or money.get("position_count") != len(money.get("positions", []))
            or anchor.get("contract") != "native_tick_anchor_diagnostic_v1"
            or money.get("account_currency") != contract["account"]["currency"]
            or manifest.get("output_sha256") != digest(shadow_slice_path)
            or contract["account"]["server"] != anchor["scope"]["server"]
            or anchor.get("clock_admitted") is not False):
        raise ValueError("account snapshot source identity mismatch")
    contract_hashes = [sha for name, sha in money["inputs_sha256"].items()
                       if Path(name).resolve() == broker_contract_path.resolve()]
    if len(contract_hashes) != 1 or contract_hashes[0] != digest(broker_contract_path):
        raise ValueError("money contract changed since closed-profit audit")
    anchor_hashes = [sha for name, sha in money["inputs_sha256"].items()
                     if Path(name).resolve() == native_anchor_path.resolve()]
    if len(anchor_hashes) != 1 or anchor_hashes[0] != digest(native_anchor_path):
        raise ValueError("native anchor changed since closed-profit audit")
    watched = {str(path): digest(path) for path in (native_money_path, native_anchor_path,
               broker_contract_path, shadow_slice_path, shadow_manifest_path, Path(__file__))}
    start = datetime.fromisoformat(anchor["scope"]["source_epoch_start"])
    end = datetime.fromisoformat(anchor["scope"]["source_epoch_end_exclusive"])
    if start.utcoffset() != timedelta(0) or end - start > timedelta(days=14):
        raise ValueError("unbounded raw price window")
    market = _tape(raw_dir, anchor, "XAUUSD", start, end, watched)
    conversion = _tape(raw_dir, anchor, "EURUSD", start, end, watched)
    positions = money["positions"]
    if len({row["position_id"] for row in positions}) != len(positions):
        raise ValueError("duplicate native money position")
    direct_days = anchor["independent_clock_evidence"]["days"]
    rows, seen, count = [], set(), 0
    with gzip.open(shadow_slice_path, "rt", encoding="utf-8") as stream:
        for count, line in enumerate(stream, 1):
            if count > MAX_EVENTS:
                raise ValueError("shadow event budget exceeded")
            event = json.loads(line)
            if event.get("ev") != "mt5_account_connected":
                continue
            if (event.get("event_id") in seen or event.get("server") != contract["account"]["server"]
                    or event.get("currency") != contract["account"]["currency"]):
                raise ValueError("account event identity mismatch")
            seen.add(event["event_id"])
            stamp = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
                raise ValueError("account event clock not UTC")
            broker_day = datetime.fromtimestamp(stamp.timestamp() + 10_800, timezone.utc).date().isoformat()
            clock_status = direct_days.get(broker_day, {}).get("status", "outside_extracted_window")
            row = snapshot_row(event, positions, market, conversion, offset_seconds=10_800,
                               clock_status=clock_status,
                               max_fx_age_ms=contract["conversion"]["max_quote_age_ms"],
                               contract_size=contract["instrument"]["contract_size"])
            row["source_epoch_day"] = broker_day
            rows.append(row)
    if count != manifest.get("selected_lines"):
        raise ValueError("shadow selected-line count differs from manifest")
    for path, sha in watched.items():
        if digest(path) != sha:
            raise ValueError("account snapshot source changed during run")
    return {"contract": "native_account_snapshot_diagnostic_v1", "status": "diagnostic_only",
            "clock_admitted": False, "full_trajectory_parity_verified": False,
            "assumed_offset_seconds": 10_800, "account_event_count": len(rows),
            "statuses": dict(Counter(row["status"] for row in rows)),
            "rows": rows, "inputs_sha256": watched,
            "limitations": ["Account event timestamp follows account_info read; acquisition time was not logged.",
                            "Account credit and other adjustments were not captured; equity minus balance is not proven pure floating profit.",
                            "Broker offset is direct only on listed days, a hypothesis on the others.",
                            "Historical marks are not actual MT5 account_info floating snapshots.",
                            "Startup snapshots are sparse and cannot establish peak drawdown or full path parity."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--native-money", type=Path, required=True)
    parser.add_argument("--native-anchor", type=Path, required=True)
    parser.add_argument("--broker-contract", type=Path, required=True)
    parser.add_argument("--shadow-slice", type=Path, required=True)
    parser.add_argument("--shadow-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("immutable account snapshot report already exists")
    result = audit(args.raw_dir, args.native_money, args.native_anchor, args.broker_contract,
                   args.shadow_slice, args.shadow_manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "events": result["account_event_count"],
                      "statuses": result["statuses"]}), flush=True)


if __name__ == "__main__":
    main()
