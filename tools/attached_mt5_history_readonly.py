"""Read-only history from the running bot terminal (ticks only with the market closed).

The isolated copy (tools/isolated_mt5_history.py) cannot finish its IPC start
inside the VM service session (-10005 IPC timeout, 25/09/2026), so this tool
attaches to the bot's own terminal instead. It never selects symbols, never
sends or modifies orders, refuses non-demo accounts and refuses tick reads
while quotes are still moving. Outputs follow raw_isolated_mt5_history_v1
(per-day Parquet, full-day vs two half-day consistency check) plus one JSON
with the account's deals and orders.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolated_mt5_history as imh  # noqa: E402

EXPECTED_SERVER = "VantageMarkets-Demo"
SYMBOLS = ("XAUUSD", "EURUSD")
MARKET_CLOSED_WAIT_S = 30


def attach(mt5):
    if not mt5.initialize(timeout=10000):
        raise RuntimeError(f"attach failed {mt5.last_error()}")
    terminal, account = mt5.terminal_info(), mt5.account_info()
    if terminal is None or account is None:
        raise RuntimeError("terminal or account unavailable")
    if account.server != EXPECTED_SERVER or account.trade_mode != 0:
        raise RuntimeError("wrong server or non-demo account")
    if not terminal.connected:
        raise RuntimeError("terminal not connected")
    return {"mode": "attached_live_terminal_readonly", "server": account.server, "demo": True,
            "currency": account.currency, "leverage": account.leverage, "build": terminal.build,
            "terminal_path": terminal.path, "data_path": terminal.data_path,
            "account_binding_sha256": hashlib.sha256(f"{account.server}:{account.login}".encode()).hexdigest(),
            "mt5_python_version": mt5.__version__, "positions_total": mt5.positions_total()}


def market_closed(mt5, wait_s=MARKET_CLOSED_WAIT_S):
    """Closed only if neither symbol publishes a new quote during wait_s.

    Tick epochs are broker-server time, so their age against the VM clock is
    not trusted here; a frozen time_msc is.
    """
    first = {}
    for symbol in SYMBOLS:
        info = mt5.symbol_info(symbol)
        if info is None or not info.visible:
            raise RuntimeError(f"{symbol} not in Market Watch; refusing to select it")
        tick = mt5.symbol_info_tick(symbol)
        first[symbol] = None if tick is None else int(tick.time_msc)
    time.sleep(wait_s)
    second = {}
    for symbol in SYMBOLS:
        tick = mt5.symbol_info_tick(symbol)
        second[symbol] = None if tick is None else int(tick.time_msc)
    frozen = all(first[s] is not None and first[s] == second[s] for s in SYMBOLS)
    return frozen, {"first_time_msc": first, "second_time_msc": second, "wait_s": wait_s}


def extract_ticks(mt5, binding, output, start, end):
    import pandas as pd
    for symbol in SYMBOLS:
        info = mt5.symbol_info(symbol)
        fields = ("name", "path", "currency_base", "currency_profit", "currency_margin", "digits", "point",
                  "trade_contract_size", "trade_tick_size", "trade_calc_mode", "volume_min", "volume_max", "volume_step")
        imh.immutable_json(output / symbol / "current_symbol_metadata.json",
                           {"historical_contract_verified": False, **{k: getattr(info, k) for k in fields}})
        day = start
        while day < end:
            label = day.date().isoformat()
            stop, middle = day + timedelta(days=1), day + timedelta(hours=12)
            frames, errors = [], []
            for left, right in ((day, stop), (day, middle), (middle, stop)):
                raw = mt5.copy_ticks_range(symbol, left, right, mt5.COPY_TICKS_ALL)
                errors.append(mt5.last_error()[0])
                frames.append(imh.frame_for_interval(raw, int(left.timestamp() * 1000), int(right.timestamp() * 1000)))
            full, first, second = frames
            row = {"symbol": symbol, "server": binding["server"], "source_epoch_day": label,
                   "query_error_codes": errors, "engine_admitted": False, "artifact": None,
                   "clock_admitted": False, "extracted_at_utc": datetime.now(timezone.utc).isoformat()}
            read_ok = all(code in (1, -4) for code in errors)
            if full is None or full.empty:
                row.update(status="no_ticks_returned" if read_ok else "query_error", rows=0,
                           reads_consistent=False, session_closed_proved=False)
            else:
                halves = pd.concat([f if f is not None else full.iloc[:0] for f in (first, second)], ignore_index=True)
                consistent = read_ok and full.equals(halves)
                row.update(imh.inspect_ticks(full), reads_consistent=consistent,
                           status="raw_reads_consistent" if consistent else "raw_reads_disagree")
                artifact = output / symbol / f"{label}.parquet"
                full.to_parquet(artifact, index=False, compression="zstd")
                row.update(artifact=artifact.name, sha256=imh.digest(artifact), bytes=artifact.stat().st_size)
            imh.immutable_json(output / symbol / f"{label}.json", row)
            print(json.dumps({"event": "day_saved", "symbol": symbol, "day": label,
                              "rows": row["rows"], "status": row["status"]}), flush=True)
            day = stop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end-exclusive", required=True)
    parser.add_argument("--deals-only", action="store_true")
    args = parser.parse_args()
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    import MetaTrader5 as mt5
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("Refusing to overwrite evidence")
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end_exclusive).replace(tzinfo=timezone.utc)
    if not 0 < (end - start).days <= 31 or start.hour != 0 or end.hour != 0:
        raise SystemExit("Require whole days, at most 31")
    try:
        binding = attach(mt5)
        closed, ages = market_closed(mt5, wait_s=5 if args.deals_only else MARKET_CLOSED_WAIT_S)
        binding["quote_freeze_check"] = ages
        if not args.deals_only and not closed:
            raise RuntimeError(f"market still quoting {ages}; tick reads only after the close")
        output.mkdir(parents=True)
        contract = {"schema_version": "raw_isolated_mt5_history_v1", "server": binding["server"],
                    "access_mode": "attached_live_terminal_readonly",
                    "source_epoch_start": start.isoformat(), "source_epoch_end_exclusive": end.isoformat(),
                    "symbols": [] if args.deals_only else list(SYMBOLS), "clock_admitted": False,
                    "engine_dataset_ready": False, "provider_prices_used": False,
                    "timezone_conversion_applied": False,
                    "day_verification": "full_day_vs_two_half_days_all_raw_fields",
                    "implementation_sha256": imh.digest(__file__),
                    "helper_implementation_sha256": imh.digest(imh.__file__)}
        imh.immutable_json(output / "contract.json", contract)
        imh.immutable_json(output / "binding.json", binding)
        deals = mt5.history_deals_get(start, end)
        orders = mt5.history_orders_get(start, end)
        if deals is None or orders is None:
            raise RuntimeError(f"history query failed {mt5.last_error()}")
        history = {"schema_version": "attached_account_history_v1",
                   "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                   "server": binding["server"], "currency": binding["currency"],
                   "account_binding_sha256": binding["account_binding_sha256"],
                   "query_start": start.isoformat(), "query_end": end.isoformat(),
                   "timestamp_semantics": "native broker timestamps, not relabelled as UTC",
                   "deals": [d._asdict() for d in deals], "orders": [o._asdict() for o in orders]}
        with (output / "account_history.json").open("x", encoding="utf-8") as stream:
            json.dump(history, stream, indent=2)
        print(json.dumps({"event": "history_saved", "deals": len(deals), "orders": len(orders)}), flush=True)
        if not args.deals_only:
            extract_ticks(mt5, binding, output, start, end)
        print(json.dumps({"status": "finished"}), flush=True)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
