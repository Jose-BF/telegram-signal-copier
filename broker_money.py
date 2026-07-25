"""Exact account-currency conversion for provider-first strategy replay."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from tools import ensure_replay_tick_cache


SCHEMA_VERSION = 1
SUPPORTED_CALC_MODES = {4}
SUPPORTED_ORIENTATIONS = {
    "account_base_profit_quote",
    "profit_base_account_quote",
    "identity",
}


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _parse_utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _stable_strings(items: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(str(item) for item in items if str(item)))


def load_contract(path: Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("broker money contract must be an object")
    return payload


def validate_contract_metadata(contract: dict) -> list[str]:
    blockers: list[str] = []
    account = contract.get("account") or {}
    instrument = contract.get("instrument") or {}
    conversion = contract.get("conversion") or {}
    costs = contract.get("costs") or {}
    live_validation = contract.get("live_validation") or {}

    if contract.get("schema_version") != SCHEMA_VERSION:
        blockers.append("unsupported_money_contract_schema")
    if not account.get("currency"):
        blockers.append("missing_account_currency")
    digits = account.get("currency_digits")
    if not isinstance(digits, int) or not 0 <= digits <= 8:
        blockers.append("invalid_account_currency_digits")
    if not instrument.get("symbol"):
        blockers.append("missing_instrument_symbol")
    if instrument.get("trade_calc_mode") not in SUPPORTED_CALC_MODES:
        blockers.append("unsupported_trade_calc_mode")
    for key in ("contract_size", "tick_size"):
        value = _decimal(instrument.get(key))
        if value is None or value <= 0:
            blockers.append(f"invalid_instrument_{key}")
    if not instrument.get("currency_profit"):
        blockers.append("missing_profit_currency")
    orientation = conversion.get("orientation")
    if orientation not in SUPPORTED_ORIENTATIONS:
        blockers.append("unsupported_conversion_orientation")
    if orientation != "identity" and not conversion.get("symbol"):
        blockers.append("missing_conversion_symbol")
    max_age = conversion.get("max_quote_age_ms")
    if not isinstance(max_age, int) or max_age <= 0:
        blockers.append("invalid_conversion_quote_age")
    max_interval = conversion.get("max_quote_interval_ms", max_age)
    if (
        not isinstance(max_interval, int)
        or max_interval <= 0
        or (isinstance(max_age, int) and max_interval < max_age)
    ):
        blockers.append("invalid_conversion_quote_interval")
    if costs.get("commission_model") != "observed_zero_intraday":
        blockers.append("unsupported_commission_model")
    if costs.get("fee_model") != "observed_zero_intraday":
        blockers.append("unsupported_fee_model")
    if costs.get("swap_model") != "intraday_only_zero":
        blockers.append("unsupported_swap_model")
    if live_validation.get("valid") is not True:
        blockers.append("live_tick_value_validation_failed")
    return _stable_strings(blockers)


class VerifiedConversionTickCache:
    def __init__(self, cache_dir: Path, *, symbol: str):
        self.cache_dir = Path(cache_dir)
        self.symbol = str(symbol)
        self._frames: dict[str, pd.DataFrame] = {}

    def load_day(self, day: date) -> tuple[pd.DataFrame, str | None]:
        day_text = day.isoformat()
        if day_text in self._frames:
            return self._frames[day_text], None
        contract = ensure_replay_tick_cache.load_valid_day_contract(
            self.cache_dir,
            day,
            expected_symbol=self.symbol,
        )
        if contract is None:
            return pd.DataFrame(), f"invalid_conversion_tick_contract:{day_text}"
        if contract.get("symbol") != self.symbol:
            return pd.DataFrame(), f"conversion_tick_symbol_mismatch:{day_text}"
        path = self.cache_dir / f"{day_text}.parquet"
        try:
            frame = pd.read_parquet(
                path,
                columns=["time_utc", "bid", "ask"],
            )
        except Exception as exc:
            return pd.DataFrame(), (
                f"conversion_tick_read_failed:{day_text}:{type(exc).__name__}"
            )
        if frame.empty:
            return pd.DataFrame(), f"empty_conversion_ticks:{day_text}"
        frame = frame.copy()
        frame["time_utc"] = pd.to_datetime(
            frame["time_utc"],
            utc=True,
            errors="coerce",
        )
        frame["bid"] = pd.to_numeric(frame["bid"], errors="coerce")
        frame["ask"] = pd.to_numeric(frame["ask"], errors="coerce")
        frame = frame.dropna(subset=["time_utc", "bid", "ask"])
        frame = frame.loc[(frame["bid"] > 0) & (frame["ask"] > 0)]
        frame = frame.sort_values(
            "time_utc",
            kind="stable",
        ).reset_index(drop=True)
        if frame.empty:
            return pd.DataFrame(), f"invalid_conversion_quotes:{day_text}"
        self._frames[day_text] = frame
        return frame, None


class BrokerMoneyConverter:
    def __init__(
        self,
        contract: dict,
        *,
        tick_cache_dir: Path | None = None,
        quote_loader: Callable[[date], tuple[pd.DataFrame, str | None]] | None = None,
    ):
        self.contract = deepcopy(contract)
        blockers = validate_contract_metadata(self.contract)
        if blockers:
            raise ValueError(",".join(blockers))
        self.account = self.contract["account"]
        self.instrument = self.contract["instrument"]
        self.conversion = self.contract["conversion"]
        self.currency = str(self.account["currency"])
        self.currency_digits = int(self.account["currency_digits"])
        self.quantum = Decimal(1).scaleb(-self.currency_digits)
        if quote_loader is None:
            if tick_cache_dir is None:
                raise ValueError("money conversion tick cache is required")
            cache = VerifiedConversionTickCache(
                tick_cache_dir,
                symbol=str(self.conversion.get("symbol") or ""),
            )
            quote_loader = cache.load_day
        self.quote_loader = quote_loader

    def _money(self, value: Decimal) -> Decimal:
        return value.quantize(self.quantum, rounding=ROUND_HALF_UP)

    def _quote_at(
        self,
        close_dt: datetime,
        profit_currency_pnl: Decimal,
    ) -> tuple[dict | None, list[str]]:
        orientation = self.conversion["orientation"]
        if orientation == "identity":
            return {
                "symbol": None,
                "side": "identity",
                "price": 1.0,
                "time_utc": close_dt.isoformat(),
                "age_ms": 0,
            }, []

        frame, error = self.quote_loader(close_dt.date())
        symbol = str(self.conversion["symbol"])
        if error is not None:
            return None, [error]
        if frame is None or frame.empty:
            return None, [f"missing_conversion_ticks:{symbol}"]
        prepared = frame
        if not pd.api.types.is_datetime64_any_dtype(prepared["time_utc"]):
            prepared = prepared.copy()
            prepared["time_utc"] = pd.to_datetime(
                prepared["time_utc"],
                utc=True,
                errors="coerce",
            )
        prepared = prepared.sort_values(
            "time_utc",
            kind="stable",
        ).reset_index(drop=True)
        eligible = prepared.loc[
            prepared["time_utc"] <= pd.Timestamp(close_dt)
        ]
        if eligible.empty:
            return None, [f"missing_prior_conversion_quote:{symbol}"]
        quote = eligible.iloc[-1]
        quote_dt = pd.Timestamp(quote["time_utc"]).to_pydatetime()
        age_ms = int(round((close_dt - quote_dt).total_seconds() * 1000))
        if age_ms < 0:
            return None, [f"future_conversion_quote:{symbol}"]
        freshness = "within_max_age"
        quote_interval_ms = None
        next_quote_utc = None
        if age_ms > int(self.conversion["max_quote_age_ms"]):
            quote_index = int(eligible.index[-1])
            quote_position = quote_index
            if quote_position + 1 >= len(prepared):
                return None, [f"stale_conversion_quote:{symbol}"]
            next_quote = prepared.iloc[quote_position + 1]
            next_quote_dt = pd.Timestamp(
                next_quote["time_utc"]
            ).to_pydatetime()
            quote_interval_ms = int(round(
                (next_quote_dt - quote_dt).total_seconds() * 1000
            ))
            max_interval_ms = int(
                self.conversion.get(
                    "max_quote_interval_ms",
                    self.conversion["max_quote_age_ms"],
                )
            )
            if (
                quote_interval_ms <= 0
                or quote_interval_ms > max_interval_ms
                or close_dt >= next_quote_dt
            ):
                return None, [f"stale_conversion_quote:{symbol}"]
            freshness = "bracketed_tick_interval"
            next_quote_utc = next_quote_dt.astimezone(timezone.utc).isoformat()

        positive = profit_currency_pnl >= 0
        if orientation == "account_base_profit_quote":
            side = "ask" if positive else "bid"
        else:
            side = "bid" if positive else "ask"
        price = _decimal(quote.get(side))
        if price is None or price <= 0:
            return None, [f"invalid_conversion_quote:{symbol}:{side}"]
        return {
            "symbol": symbol,
            "side": side,
            "price": float(price),
            "time_utc": quote_dt.astimezone(timezone.utc).isoformat(),
            "age_ms": age_ms,
            "freshness": freshness,
            "quote_interval_ms": quote_interval_ms,
            "next_quote_utc": next_quote_utc,
        }, []

    def convert_leg(
        self,
        *,
        direction: str,
        open_price: object,
        close_price: object,
        volume: object,
        open_time_utc: object,
        close_time_utc: object,
        allow_overnight: bool = False,
    ) -> dict:
        blockers: list[str] = []
        opened = _parse_utc(open_time_utc)
        closed = _parse_utc(close_time_utc)
        open_value = _decimal(open_price)
        close_value = _decimal(close_price)
        volume_value = _decimal(volume)
        contract_size = _decimal(self.instrument.get("contract_size"))
        direction = str(direction or "").upper()

        if direction not in {"BUY", "SELL"}:
            blockers.append("invalid_money_direction")
        if opened is None:
            blockers.append("invalid_money_open_time")
        if closed is None:
            blockers.append("invalid_money_close_time")
        if open_value is None or open_value <= 0:
            blockers.append("invalid_money_open_price")
        if close_value is None or close_value <= 0:
            blockers.append("invalid_money_close_price")
        if volume_value is None or volume_value <= 0:
            blockers.append("invalid_money_volume")
        if contract_size is None or contract_size <= 0:
            blockers.append("invalid_money_contract_size")
        if opened is not None and closed is not None:
            if closed < opened:
                blockers.append("money_close_before_open")
            if closed.date() != opened.date() and not allow_overnight:
                blockers.append("overnight_cost_model_unverified")
        if blockers:
            return {
                "status": "blocked",
                "strategy_pnl": None,
                "pnl_currency": self.currency,
                "profit_currency_pnl": None,
                "conversion": None,
                "blockers": _stable_strings(blockers),
            }

        price_delta = (
            close_value - open_value
            if direction == "BUY"
            else open_value - close_value
        )
        profit_currency_pnl = price_delta * contract_size * volume_value
        if profit_currency_pnl == 0:
            return {
                "status": "verified",
                "strategy_pnl": 0.0,
                "pnl_currency": self.currency,
                "profit_currency_pnl": 0.0,
                "conversion": {
                    "symbol": self.conversion.get("symbol"),
                    "side": "not_required_zero",
                    "price": None,
                    "time_utc": closed.isoformat(),
                    "age_ms": 0,
                    "freshness": "not_required_zero",
                },
                "formula": {
                    "directional_delta": float(price_delta),
                    "contract_size": float(contract_size),
                    "volume": float(volume_value),
                },
                "blockers": [],
            }
        conversion, quote_blockers = self._quote_at(
            closed,
            profit_currency_pnl,
        )
        if quote_blockers:
            return {
                "status": "blocked",
                "strategy_pnl": None,
                "pnl_currency": self.currency,
                "profit_currency_pnl": float(profit_currency_pnl),
                "conversion": None,
                "blockers": quote_blockers,
            }

        quote = _decimal(conversion["price"])
        orientation = self.conversion["orientation"]
        if orientation == "account_base_profit_quote":
            account_pnl = profit_currency_pnl / quote
        elif orientation == "profit_base_account_quote":
            account_pnl = profit_currency_pnl * quote
        else:
            account_pnl = profit_currency_pnl
        account_pnl = self._money(account_pnl)
        return {
            "status": "verified",
            "strategy_pnl": float(account_pnl),
            "pnl_currency": self.currency,
            "profit_currency_pnl": float(
                profit_currency_pnl.quantize(
                    Decimal("0.00000001"),
                    rounding=ROUND_HALF_UP,
                )
            ),
            "conversion": conversion,
            "formula": {
                "directional_delta": float(price_delta),
                "contract_size": float(contract_size),
                "volume": float(volume_value),
                "orientation": orientation,
                "rounding": "ROUND_HALF_UP",
                "currency_digits": self.currency_digits,
            },
            "blockers": [],
        }


def apply_money_contract(
    row: dict,
    *,
    direction: str,
    converter: BrokerMoneyConverter,
) -> dict:
    result = deepcopy(row)
    if result.get("status") == "blocked":
        result["money_status"] = "not_applicable"
        return result

    money_blockers: list[str] = []
    converted_legs: list[dict] = []
    pnls: list[Decimal] = []
    for leg in result.get("legs") or []:
        converted = dict(leg)
        if leg.get("status") != "simulated":
            converted_legs.append(converted)
            continue
        money = converter.convert_leg(
            direction=direction,
            open_price=leg.get("open_price"),
            close_price=leg.get("close_price"),
            volume=leg.get("volume"),
            open_time_utc=leg.get("open_time_utc"),
            close_time_utc=leg.get("close_time_utc"),
        )
        converted["money_status"] = money["status"]
        converted["strategy_pnl"] = money["strategy_pnl"]
        converted["pnl_currency"] = money["pnl_currency"]
        converted["profit_currency_pnl"] = money["profit_currency_pnl"]
        converted["money_conversion"] = money["conversion"]
        converted["money_blockers"] = money["blockers"]
        converted_legs.append(converted)
        money_blockers.extend(money["blockers"])
        if money["strategy_pnl"] is not None:
            pnls.append(Decimal(str(money["strategy_pnl"])))

    result["legs"] = converted_legs
    result["pnl_currency"] = converter.currency
    result["money_blockers"] = _stable_strings(money_blockers)
    expected_legs = sum(
        leg.get("status") == "simulated"
        for leg in result.get("legs") or []
    )
    if money_blockers or len(pnls) != expected_legs:
        result["money_status"] = "blocked"
        result["strategy_pnl"] = None
    else:
        result["money_status"] = "verified"
        result["strategy_pnl"] = float(
            sum(pnls, Decimal("0")).quantize(
                converter.quantum,
                rounding=ROUND_HALF_UP,
            )
        )
    return result


def _actual_close_time_utc(ticket: dict) -> object:
    """Recover deal milliseconds when the ledger close time is second-based."""
    coarse = _parse_utc(ticket.get("close_dt_utc"))
    raw_msc = (ticket.get("close_deal") or {}).get("time_msc")
    try:
        if coarse is None or raw_msc is None:
            return ticket.get("close_dt_utc")
        raw = datetime.fromtimestamp(
            float(raw_msc) / 1000.0,
            tz=timezone.utc,
        )
        offset_hours = round((raw - coarse).total_seconds() / 3600.0)
        normalized = raw - timedelta(hours=offset_hours)
        if abs((normalized - coarse).total_seconds()) <= 2:
            return normalized
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return ticket.get("close_dt_utc")


def validate_executed_money_contract(
    trades: Iterable[dict],
    converter: BrokerMoneyConverter,
) -> dict:
    rows: list[dict] = []
    blockers: list[str] = []
    for trade in trades:
        direction = str(trade.get("direction") or "")
        for ticket in trade.get("tickets") or []:
            label = str(ticket.get("ticket") or "unknown")
            if not ticket.get("is_closed"):
                rows.append({
                    "sig_id": trade.get("sig_id"),
                    "ticket": ticket.get("ticket"),
                    "status": "blocked",
                    "expected": None,
                    "observed": None,
                    "difference": None,
                    "blockers": [f"actual_ticket_not_closed:{label}"],
                })
                continue
            components = ticket.get("pnl_components") or {}
            nonzero_costs = [
                name
                for name in ("commission", "fee")
                if (_decimal(components.get(name)) or Decimal("0")) != 0
            ]
            if nonzero_costs:
                rows.append({
                    "sig_id": trade.get("sig_id"),
                    "ticket": ticket.get("ticket"),
                    "status": "blocked",
                    "expected": None,
                    "observed": ticket.get("pnl_net"),
                    "difference": None,
                    "blockers": [
                        f"unsupported_nonzero_costs:{label}:"
                        + ",".join(nonzero_costs)
                    ],
                })
                continue

            money = converter.convert_leg(
                direction=direction,
                open_price=ticket.get("open_price"),
                close_price=ticket.get("close_price"),
                volume=ticket.get("volume"),
                open_time_utc=ticket.get("open_dt_utc"),
                close_time_utc=_actual_close_time_utc(ticket),
                allow_overnight=True,
            )
            if money["status"] != "verified":
                rows.append({
                    "sig_id": trade.get("sig_id"),
                    "ticket": ticket.get("ticket"),
                    "status": "blocked",
                    "expected": None,
                    "observed": ticket.get("pnl_net"),
                    "difference": None,
                    "blockers": money["blockers"],
                })
                continue

            observed = _decimal(components.get("net"))
            if observed is None:
                observed = _decimal(ticket.get("pnl_net"))
            swap = _decimal(components.get("swap")) or Decimal("0")
            expected = _decimal(money["strategy_pnl"])
            if expected is not None:
                expected = (expected + swap).quantize(
                    converter.quantum,
                    rounding=ROUND_HALF_UP,
                )
            if observed is None or expected is None:
                rows.append({
                    "sig_id": trade.get("sig_id"),
                    "ticket": ticket.get("ticket"),
                    "status": "blocked",
                    "expected": money["strategy_pnl"],
                    "observed": ticket.get("pnl_net"),
                    "difference": None,
                    "blockers": [f"missing_actual_money:{label}"],
                })
                continue
            observed = observed.quantize(
                converter.quantum,
                rounding=ROUND_HALF_UP,
            )
            difference = (expected - observed).quantize(
                converter.quantum,
                rounding=ROUND_HALF_UP,
            )
            rows.append({
                "sig_id": trade.get("sig_id"),
                "ticket": ticket.get("ticket"),
                "status": "exact" if difference == 0 else "mismatch",
                "expected": float(expected),
                "observed": float(observed),
                "difference": float(difference),
                "blockers": [],
            })

    exact = sum(row["status"] == "exact" for row in rows)
    mismatched = sum(row["status"] == "mismatch" for row in rows)
    blocked = sum(row["status"] == "blocked" for row in rows)
    if not rows:
        blockers.append("no_actual_tickets_for_money_validation")
    if mismatched:
        blockers.append(f"actual_money_reconciliation_mismatch:{mismatched}")
    if blocked:
        blockers.append(f"actual_money_reconciliation_blocked:{blocked}")
    return {
        "verified": bool(rows) and not blockers and exact == len(rows),
        "account_currency": converter.currency,
        "tickets_checked": len(rows),
        "exact_tickets": exact,
        "mismatched_tickets": mismatched,
        "blocked_tickets": blocked,
        "blockers": _stable_strings(blockers),
        "rows": rows,
    }

