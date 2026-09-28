"""Check native closed-position EUR profits against causal historical FX ticks."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from broker_money import convert_profit_amount, validate_contract_metadata


CENT = Decimal("0.01")
MAX_DEALS = 10_000
MAX_FX_ROWS = 2_000_000


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    path = Path(path)
    if path.stat().st_size > 16_000_000:
        raise ValueError("money audit JSON budget exceeded")
    return json.loads(path.read_text(encoding="utf-8"))


def cents(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("invalid native money")
    rounded = number.quantize(CENT, rounding=ROUND_HALF_UP)
    if abs(number - rounded) > Decimal("0.00000001"):
        raise ValueError("native money exceeds cent precision")
    return rounded


def _anchored_hash(anchor, path):
    matches = [sha for name, sha in anchor["input_sha256"].items()
               if Path(name).resolve() == Path(path).resolve()]
    if len(matches) != 1 or digest(path) != matches[0]:
        raise ValueError(f"source not bound to native anchor audit: {path}")


def money_rows(native, reconciled, fx_times, fx_bid, fx_ask, *, contract):
    """Retain every reconciled position, including missing FX and mismatches."""
    groups = defaultdict(list)
    for deal in native["deals"]:
        if deal.get("symbol") == "XAUUSD":
            groups[deal["position_id"]].append(deal)
    positions = {row["position_id"]: row for row in reconciled["positions"]}
    if len(positions) != len(reconciled["positions"]) or set(positions) != set(groups):
        raise ValueError("native position denominator differs from reconciliation")
    digits = contract["account"]["currency_digits"]
    size = Decimal(str(contract["instrument"]["contract_size"]))
    max_age = contract["conversion"]["max_quote_age_ms"]
    max_interval = contract["conversion"].get("max_quote_interval_ms", max_age)
    rows = []
    for position_id, deals in sorted(groups.items()):
        info = positions[position_id]
        row = {"position_id": position_id, "signal_id": info["signal_id"],
               "channel": info["signal_id"].split("_", 1)[0],
               "deal_tickets": [deal["ticket"] for deal in deals]}
        entries = [deal for deal in deals if deal["entry"] == 0]
        exits = [deal for deal in deals if deal["entry"] == 1]
        if (len(entries) != 1 or len(exits) != 1
                or sorted(row["deal_tickets"]) != sorted(info["deal_tickets"])):
            row["status"] = "blocked_native_position_shape"
            rows.append(row)
            continue
        entry, exit_deal = entries[0], exits[0]
        row.update(entry_msc=entry["time_msc"], exit_msc=exit_deal["time_msc"],
                   direction="BUY" if entry["type"] == 0 else "SELL",
                   volume=entry["volume"], entry_price=entry["price"], exit_price=exit_deal["price"])
        if (entry["type"] not in (0, 1) or exit_deal["type"] != 1 - entry["type"]
                or entry["time_msc"] > exit_deal["time_msc"]
                or Decimal(str(entry["volume"])) != Decimal(str(exit_deal["volume"]))):
            row["status"] = "blocked_native_deal_facts"
            rows.append(row)
            continue
        actual_profit = cents(exit_deal["profit"])
        costs = sum((cents(deal[key]) for deal in deals for key in ("commission", "swap", "fee")), Decimal(0))
        actual_net = actual_profit + cents(entry["profit"]) + costs
        if actual_net != cents(info["net_eur"]):
            raise ValueError("native position money differs from reconciliation")
        row.update(actual_profit_eur=str(actual_profit), actual_net_eur=str(actual_net),
                   costs_eur=str(costs))
        if any(cents(deal["swap"]) for deal in deals):
            row["status"] = "blocked_swap_accrual_path"
            rows.append(row)
            continue
        raw = ((Decimal(str(exit_deal["price"])) - Decimal(str(entry["price"])))
               * (1 if entry["type"] == 0 else -1) * Decimal(str(entry["volume"])) * size)
        row["raw_profit_usd"] = str(raw)
        index = int(np.searchsorted(fx_times, exit_deal["time_msc"], side="right")) - 1
        if index < 0:
            row["status"] = "blocked_missing_prior_fx"
            rows.append(row)
            continue
        age = exit_deal["time_msc"] - int(fx_times[index])
        row.update(fx_tick_msc=int(fx_times[index]), fx_age_ms=age,
                   fx_bid=float(fx_bid[index]), fx_ask=float(fx_ask[index]))
        if age > max_age:
            following = index + 1
            interval = int(fx_times[following]) - int(fx_times[index]) if following < len(fx_times) else None
            if interval is None or interval <= 0 or interval > max_interval or exit_deal["time_msc"] >= int(fx_times[following]):
                row["status"] = "blocked_stale_fx"
                rows.append(row)
                continue
            row.update(fx_freshness="retrospectively_bracketed", fx_next_tick_msc=int(fx_times[following]),
                       fx_interval_ms=interval, within_strict_causal_age=False)
        else:
            row.update(fx_freshness="within_max_age", within_strict_causal_age=True)
        rate = Decimal(str(fx_ask[index] if raw >= 0 else fx_bid[index]))
        predicted = convert_profit_amount(raw, rate, orientation=contract["conversion"]["orientation"],
                                          currency_digits=digits)
        delta = predicted - actual_profit
        row.update(predicted_profit_eur=str(predicted), delta_eur=str(delta),
                   status="exact_exit_money" if delta == 0 else "exit_money_mismatch")
        rows.append(row)
    return rows


def audit(raw_dir, native_path, reconciled_path, contract_path, anchor_path):
    raw_dir, native_path, reconciled_path, contract_path, anchor_path = map(
        Path, (raw_dir, native_path, reconciled_path, contract_path, anchor_path))
    anchor, native, reconciled, contract = map(read, (anchor_path, native_path, reconciled_path, contract_path))
    if (anchor.get("contract") != "native_tick_anchor_diagnostic_v1"
            or anchor.get("clock_admitted") is not False
            or anchor.get("engine_dataset_ready") is not False
            or reconciled.get("source_sha256") != digest(native_path)
            or native.get("server") != contract.get("account", {}).get("server")
            or native.get("currency") != contract.get("account", {}).get("currency")
            or len(native.get("deals", [])) > MAX_DEALS):
        raise ValueError("native money audit identity or budget mismatch")
    for path in (raw_dir / "contract.json", raw_dir / "binding.json", native_path,
                 reconciled_path, contract_path):
        _anchored_hash(anchor, path)
    blockers = validate_contract_metadata(contract)
    if blockers or contract["instrument"]["symbol"] != "XAUUSD" or contract["conversion"]["symbol"] != "EURUSD":
        raise ValueError(f"unadmitted money contract: {blockers}")
    raw_contract = read(raw_dir / "contract.json")
    if (raw_contract["server"] != native["server"] or raw_contract["source_epoch_start"] != anchor["scope"]["source_epoch_start"]
            or raw_contract["source_epoch_end_exclusive"] != anchor["scope"]["source_epoch_end_exclusive"]):
        raise ValueError("raw price range or server differs from anchor")
    fx_frames, watched = [], {str(path): digest(path) for path in
        (native_path, reconciled_path, contract_path, anchor_path, Path(__file__))}
    start = datetime.fromisoformat(raw_contract["source_epoch_start"])
    end = datetime.fromisoformat(raw_contract["source_epoch_end_exclusive"])
    if start.tzinfo is None or start.utcoffset().total_seconds() != 0 or end - start > pd.Timedelta(days=14):
        raise ValueError("invalid bounded source epoch")
    day = start
    while day < end:
        label = day.date().isoformat()
        meta_path = raw_dir / "EURUSD" / f"{label}.json"
        data_path = meta_path.with_suffix(".parquet")
        _anchored_hash(anchor, meta_path)
        _anchored_hash(anchor, data_path)
        meta = read(meta_path)
        if meta["status"] != "raw_reads_consistent" or meta["sha256"] != digest(data_path):
            raise ValueError("FX tape is not raw-verified")
        frame = pd.read_parquet(data_path, columns=["time_msc", "bid", "ask"])
        if len(frame) != meta["rows"] or len(frame) > MAX_FX_ROWS:
            raise ValueError("FX tape row budget or metadata mismatch")
        fx_frames.append(frame)
        watched[str(meta_path)], watched[str(data_path)] = digest(meta_path), digest(data_path)
        day += pd.Timedelta(days=1)
    fx = pd.concat(fx_frames, ignore_index=True)
    times = fx.time_msc.to_numpy(dtype=np.int64)
    bid, ask = fx.bid.to_numpy(dtype=float), fx.ask.to_numpy(dtype=float)
    if (np.any(np.diff(times) < 0) or not np.isfinite(bid).all() or not np.isfinite(ask).all()
            or np.any(bid <= 0) or np.any(ask < bid)):
        raise ValueError("FX tape order or prices invalid")
    rows = money_rows(native, reconciled, times, bid, ask, contract=contract)
    for path, sha in watched.items():
        if digest(path) != sha:
            raise ValueError("money audit source changed during run")
    statuses = dict(Counter(row["status"] for row in rows))
    return {"contract": "native_closed_money_anchor_v2", "status": "diagnostic_only",
            "source_clock": "broker_epoch_not_normalized_to_utc", "floating_or_drawdown_verified": False,
            "simulation_parity_verified": False, "account_currency": native["currency"],
            "positions": rows, "position_count": len(rows), "statuses": statuses,
            "inputs_sha256": watched,
            "limitations": ["Exact native deal totals are separate from modeled profit conversion.",
                            "Prior historical EURUSD ticks do not prove the broker conversion quote at execution.",
                            "Retrospective FX brackets only establish later coverage; they cannot justify live decisions at the earlier time.",
                            "No live floating, equity, margin, protection or strategy decision path is compared."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--native-deals", type=Path, required=True)
    parser.add_argument("--native-baskets", type=Path, required=True)
    parser.add_argument("--broker-contract", type=Path, required=True)
    parser.add_argument("--anchor-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("immutable audit output already exists")
    result = audit(args.raw_dir, args.native_deals, args.native_baskets, args.broker_contract, args.anchor_audit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "positions": result["position_count"],
                      "statuses": result["statuses"]}), flush=True)


if __name__ == "__main__":
    main()
