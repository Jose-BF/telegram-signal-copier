"""Capture a versioned MT5 account/symbol money contract without private IDs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

import runtime_paths


DEFAULT_OUTPUT = (
    runtime_paths.active_data_dir(REPO_DIR) / "broker_money_contract.json"
)
SCHEMA_VERSION = 1
LIVE_VALUE_TOLERANCE = 1e-8


def _conversion_route(mt5, account_currency: str, profit_currency: str) -> dict:
    if account_currency == profit_currency:
        return {
            "symbol": None,
            "orientation": "identity",
            "positive_profit_side": "identity",
            "negative_profit_side": "identity",
            "max_quote_age_ms": 5000,
            "max_quote_interval_ms": 60000,
        }

    direct = f"{account_currency}{profit_currency}"
    if mt5.symbol_info(direct) is not None:
        if not mt5.symbol_select(direct, True):
            raise RuntimeError(f"cannot select conversion symbol {direct}")
        return {
            "symbol": direct,
            "orientation": "account_base_profit_quote",
            "positive_profit_side": "ask",
            "negative_profit_side": "bid",
            "max_quote_age_ms": 5000,
            "max_quote_interval_ms": 60000,
        }

    inverse = f"{profit_currency}{account_currency}"
    if mt5.symbol_info(inverse) is not None:
        if not mt5.symbol_select(inverse, True):
            raise RuntimeError(f"cannot select conversion symbol {inverse}")
        return {
            "symbol": inverse,
            "orientation": "profit_base_account_quote",
            "positive_profit_side": "bid",
            "negative_profit_side": "ask",
            "max_quote_age_ms": 5000,
            "max_quote_interval_ms": 60000,
        }
    raise RuntimeError(
        f"no conversion symbol for {profit_currency}->{account_currency}"
    )


def _live_tick_value_validation(mt5, instrument, conversion: dict) -> dict:
    profit_per_tick = (
        float(instrument.trade_contract_size)
        * float(instrument.trade_tick_size)
    )
    orientation = conversion["orientation"]
    if orientation == "identity":
        expected_profit = profit_per_tick
        expected_loss = profit_per_tick
        quote = None
    else:
        quote = mt5.symbol_info_tick(conversion["symbol"])
        if quote is None:
            raise RuntimeError(
                f"missing live tick for {conversion['symbol']}"
            )
        bid = float(quote.bid)
        ask = float(quote.ask)
        if not all(isfinite(value) and value > 0 for value in (bid, ask)):
            raise RuntimeError("invalid live conversion quote")
        if orientation == "account_base_profit_quote":
            expected_profit = profit_per_tick / ask
            expected_loss = profit_per_tick / bid
        else:
            expected_profit = profit_per_tick * bid
            expected_loss = profit_per_tick * ask

    actual_profit = float(instrument.trade_tick_value_profit)
    actual_loss = float(instrument.trade_tick_value_loss)
    profit_delta = actual_profit - expected_profit
    loss_delta = actual_loss - expected_loss
    return {
        "valid": (
            abs(profit_delta) <= LIVE_VALUE_TOLERANCE
            and abs(loss_delta) <= LIVE_VALUE_TOLERANCE
        ),
        "expected_tick_value_profit": expected_profit,
        "actual_tick_value_profit": actual_profit,
        "tick_value_profit_delta": profit_delta,
        "expected_tick_value_loss": expected_loss,
        "actual_tick_value_loss": actual_loss,
        "tick_value_loss_delta": loss_delta,
        "conversion_bid": None if quote is None else float(quote.bid),
        "conversion_ask": None if quote is None else float(quote.ask),
    }


def build_contract(
    mt5,
    *,
    instrument_symbol: str = "XAUUSD",
    captured_at: datetime | None = None,
) -> dict:
    account = mt5.account_info()
    if account is None:
        raise RuntimeError("MT5 account_info unavailable")
    instrument = mt5.symbol_info(instrument_symbol)
    if instrument is None:
        raise RuntimeError(f"MT5 symbol unavailable: {instrument_symbol}")
    if not mt5.symbol_select(instrument_symbol, True):
        raise RuntimeError(f"cannot select instrument {instrument_symbol}")

    account_currency = str(account.currency)
    profit_currency = str(instrument.currency_profit)
    conversion = _conversion_route(
        mt5,
        account_currency,
        profit_currency,
    )
    validation = _live_tick_value_validation(mt5, instrument, conversion)
    captured_at = (captured_at or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": captured_at.isoformat(),
        "account": {
            "server": str(account.server),
            "currency": account_currency,
            "currency_digits": int(account.currency_digits),
        },
        "instrument": {
            "symbol": str(instrument.name),
            "trade_calc_mode": int(instrument.trade_calc_mode),
            "contract_size": float(instrument.trade_contract_size),
            "tick_size": float(instrument.trade_tick_size),
            "currency_profit": profit_currency,
        },
        "conversion": conversion,
        "costs": {
            "commission_model": "observed_zero_intraday",
            "fee_model": "observed_zero_intraday",
            "swap_model": "intraday_only_zero",
        },
        "live_validation": validation,
    }


def write_contract(contract: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture the current MT5 money conversion contract"
    )
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    import MetaTrader5 as mt5

    if not mt5.initialize():
        if not args.quiet:
            print(f"MT5 initialize failed: {mt5.last_error()}")
        return 1
    try:
        contract = build_contract(mt5, instrument_symbol=args.symbol)
        write_contract(contract, args.output)
    except Exception as exc:
        if not args.quiet:
            print(f"Money contract capture failed: {exc}")
        return 1
    finally:
        mt5.shutdown()

    if not args.quiet:
        status = "verified" if contract["live_validation"]["valid"] else "invalid"
        print(f"Broker money contract: {status}")
        print(f"Output: {args.output}")
    return 0 if contract["live_validation"]["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

