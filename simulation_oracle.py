"""Independent, fail-closed oracle for strategy replay certification.

This module intentionally does not import the candidate strategy or money
engines. It exists to detect disagreements, not to share their assumptions.
"""

from __future__ import annotations

import math
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd


SCHEMA_VERSION = 1
SUPPORTED_MONEY_CALC_MODES = {4}
SUPPORTED_CONVERSION_ORIENTATIONS = {
    "account_base_profit_quote",
    "profit_base_account_quote",
    "identity",
}
TICK_TIME_CONTRACT = "mt5_server_epoch_utc_v3"
TICK_SOURCE_VERIFICATION = "full_day_vs_two_half_days_v1"
TICK_CONTENT_DIGEST = "time_bid_ask_sequence_sha256_v1"


@dataclass(frozen=True)
class PreparedTickWindow:
    frame: pd.DataFrame
    times_ns: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    source_indices: np.ndarray


def _utc(value: object) -> datetime | None:
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


def _finite_positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _decimal(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _ordered_tick_content_sha256(frame: pd.DataFrame) -> str:
    """Independently fingerprint the ordered quote stream."""
    digest = hashlib.sha256()
    digest.update(TICK_CONTENT_DIGEST.encode("ascii") + b"\0")
    digest.update(str(len(frame)).encode("ascii") + b"\0")
    arrays = (
        frame["time_utc"].astype("int64").to_numpy(dtype="<i8", copy=False),
        frame["bid"].to_numpy(dtype="<f8", copy=False),
        frame["ask"].to_numpy(dtype="<f8", copy=False),
    )
    for values in arrays:
        digest.update(np.ascontiguousarray(values).tobytes())
    return digest.hexdigest()


def _blocked(*blockers: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "blockers": list(dict.fromkeys(blockers)),
    }


def _money_contract_blockers(contract: dict) -> list[str]:
    account = contract.get("account") or {}
    instrument = contract.get("instrument") or {}
    conversion = contract.get("conversion") or {}
    costs = contract.get("costs") or {}
    live_validation = contract.get("live_validation") or {}
    blockers: list[str] = []
    if contract.get("schema_version") != 1:
        blockers.append("unsupported_money_contract_schema")
    if not account.get("currency"):
        blockers.append("missing_account_currency")
    digits = account.get("currency_digits")
    if not isinstance(digits, int) or isinstance(digits, bool) or not 0 <= digits <= 8:
        blockers.append("invalid_account_currency_digits")
    if not instrument.get("symbol"):
        blockers.append("missing_instrument_symbol")
    if instrument.get("trade_calc_mode") not in SUPPORTED_MONEY_CALC_MODES:
        blockers.append("unsupported_trade_calc_mode")
    for field in ("contract_size", "tick_size"):
        value = _decimal(instrument.get(field))
        if value is None or value <= 0:
            blockers.append(f"invalid_instrument_{field}")
    if not instrument.get("currency_profit"):
        blockers.append("missing_profit_currency")
    orientation = conversion.get("orientation")
    if orientation not in SUPPORTED_CONVERSION_ORIENTATIONS:
        blockers.append("unsupported_conversion_orientation")
    if orientation != "identity" and not conversion.get("symbol"):
        blockers.append("missing_conversion_symbol")
    max_age = conversion.get("max_quote_age_ms")
    max_interval = conversion.get("max_quote_interval_ms", max_age)
    if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age <= 0:
        blockers.append("invalid_conversion_quote_age")
    if (
        not isinstance(max_interval, int)
        or isinstance(max_interval, bool)
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
    return list(dict.fromkeys(blockers))


class IndependentMoneyOracle:
    """Independent Decimal implementation of the frozen broker contract."""

    def __init__(
        self,
        contract: dict,
        *,
        quote_loader: Callable[
            [date],
            tuple[pd.DataFrame, str | None],
        ],
    ):
        blockers = _money_contract_blockers(contract)
        if blockers:
            raise ValueError(",".join(blockers))
        self.contract = contract
        self.account = contract["account"]
        self.instrument = contract["instrument"]
        self.conversion = contract["conversion"]
        self.currency = str(self.account["currency"])
        self.currency_digits = int(self.account["currency_digits"])
        self.quantum = Decimal(1).scaleb(-self.currency_digits)
        self.quote_loader = quote_loader
        self._quote_cache: dict[
            date,
            tuple[PreparedTickWindow | None, list[str]],
        ] = {}

    def _money(self, value: Decimal) -> Decimal:
        return value.quantize(self.quantum, rounding=ROUND_HALF_UP)

    def _conversion_quote(
        self,
        closed: datetime,
        profit_currency_pnl: Decimal,
    ) -> tuple[dict | None, list[str]]:
        orientation = self.conversion["orientation"]
        if orientation == "identity":
            return {
                "symbol": None,
                "side": "identity",
                "price": 1.0,
                "time_utc": closed.isoformat(),
                "age_ms": 0,
                "freshness": "identity",
            }, []

        symbol = str(self.conversion["symbol"])
        day = closed.date()
        if day not in self._quote_cache:
            frame, error = self.quote_loader(day)
            blockers: list[str] = []
            prepared = None
            if error:
                blockers.append(str(error))
            elif frame is None or frame.empty:
                blockers.append(f"missing_conversion_ticks:{symbol}")
            else:
                prepared, frame_blockers = prepare_tick_window(frame)
                blockers.extend(
                    f"invalid_conversion_ticks:{symbol}:{blocker}"
                    for blocker in frame_blockers
                )
            self._quote_cache[day] = (
                prepared if not blockers else None,
                list(dict.fromkeys(blockers)),
            )
        prepared_window, blockers = self._quote_cache[day]
        if blockers or prepared_window is None:
            return None, blockers or [
                f"missing_conversion_ticks:{symbol}"
            ]
        prepared = prepared_window.frame
        eligible = prepared.loc[
            prepared["time_utc"] <= pd.Timestamp(closed)
        ]
        if eligible.empty:
            return None, [f"missing_prior_conversion_quote:{symbol}"]
        latest_time = eligible.iloc[-1]["time_utc"]
        latest_group = eligible.loc[eligible["time_utc"] == latest_time]
        quote_time = pd.Timestamp(latest_time).to_pydatetime()
        age_ms = int(round((closed - quote_time).total_seconds() * 1000))
        if age_ms < 0:
            return None, [f"future_conversion_quote:{symbol}"]

        positive = profit_currency_pnl >= 0
        if orientation == "account_base_profit_quote":
            side = "ask" if positive else "bid"
        else:
            side = "bid" if positive else "ask"
        side_prices = latest_group[side].astype(float).tolist()
        if any(abs(value - side_prices[0]) > 1e-12 for value in side_prices[1:]):
            return None, [
                f"ambiguous_conversion_quote:{symbol}:{quote_time.isoformat()}"
            ]

        freshness = "within_max_age"
        quote_interval_ms = None
        next_quote_utc = None
        if age_ms > int(self.conversion["max_quote_age_ms"]):
            later = prepared.loc[prepared["time_utc"] > latest_time]
            if later.empty:
                return None, [f"stale_conversion_quote:{symbol}"]
            next_time = pd.Timestamp(later.iloc[0]["time_utc"]).to_pydatetime()
            quote_interval_ms = int(
                round((next_time - quote_time).total_seconds() * 1000)
            )
            if (
                quote_interval_ms <= 0
                or quote_interval_ms
                > int(self.conversion["max_quote_interval_ms"])
                or closed >= next_time
            ):
                return None, [f"stale_conversion_quote:{symbol}"]
            freshness = "bracketed_tick_interval"
            next_quote_utc = next_time.isoformat()

        price = _decimal(side_prices[0])
        if price is None or price <= 0:
            return None, [f"invalid_conversion_quote:{symbol}:{side}"]
        return {
            "symbol": symbol,
            "side": side,
            "price": float(price),
            "time_utc": quote_time.isoformat(),
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
    ) -> dict:
        direction = str(direction or "").upper()
        opened = _utc(open_time_utc)
        closed = _utc(close_time_utc)
        open_value = _decimal(open_price)
        close_value = _decimal(close_price)
        volume_value = _decimal(volume)
        contract_size = _decimal(self.instrument.get("contract_size"))
        blockers: list[str] = []
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
            elif closed.date() != opened.date():
                blockers.append("overnight_cost_model_unverified")
        if blockers:
            return {
                "status": "blocked",
                "strategy_pnl": None,
                "pnl_currency": self.currency,
                "profit_currency_pnl": None,
                "conversion": None,
                "blockers": list(dict.fromkeys(blockers)),
            }

        delta = (
            close_value - open_value
            if direction == "BUY"
            else open_value - close_value
        )
        profit_currency_pnl = delta * contract_size * volume_value
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
                    "directional_delta": float(delta),
                    "contract_size": float(contract_size),
                    "volume": float(volume_value),
                },
                "blockers": [],
            }

        conversion, quote_blockers = self._conversion_quote(
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
                "directional_delta": float(delta),
                "contract_size": float(contract_size),
                "volume": float(volume_value),
                "orientation": orientation,
                "rounding": "ROUND_HALF_UP",
                "currency_digits": self.currency_digits,
            },
            "blockers": [],
        }


class IndependentTickCache:
    """Read and verify tick artifacts without the candidate replay loader."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        expected_symbol: str,
        require_market_session: bool,
    ):
        self.cache_dir = Path(cache_dir)
        self.expected_symbol = str(expected_symbol)
        self.require_market_session = bool(require_market_session)
        self._frames: dict[str, pd.DataFrame] = {}
        self._evidence: dict[str, dict] = {}

    @property
    def evidence_by_day(self) -> dict[str, dict]:
        return {
            day: dict(evidence)
            for day, evidence in sorted(self._evidence.items())
        }

    def _contract_blockers(self, day_text: str, contract: dict) -> list[str]:
        blockers: list[str] = []
        if contract.get("tick_time_contract") != TICK_TIME_CONTRACT:
            blockers.append(f"invalid_tick_time_contract:{day_text}")
        if contract.get("time_basis") != "UTC":
            blockers.append(f"invalid_tick_time_basis:{day_text}")
        if contract.get("source_time_basis") != "mt5_server_epoch":
            blockers.append(f"invalid_tick_source_time_basis:{day_text}")
        offset = contract.get("utc_offset_seconds")
        if isinstance(offset, bool) or not isinstance(offset, int):
            blockers.append(f"invalid_tick_utc_offset:{day_text}")
        if contract.get("semantic_time_valid") is not True:
            blockers.append(f"semantic_tick_time_unverified:{day_text}")
        anchors = contract.get("anchor_validation")
        if not isinstance(anchors, dict) or anchors.get("valid") is not True:
            blockers.append(f"invalid_tick_anchor_validation:{day_text}")

        symbol = contract.get("symbol")
        if symbol is not None and str(symbol) != self.expected_symbol:
            blockers.append(f"tick_symbol_mismatch:{day_text}")
        elif symbol is None:
            checked = int((anchors or {}).get("anchors_checked") or 0)
            matched = int((anchors or {}).get("anchors_matched") or 0)
            if (
                self.expected_symbol != "XAUUSD"
                or checked <= 0
                or matched != checked
            ):
                blockers.append(f"tick_symbol_unverified:{day_text}")
        source_verification = contract.get("source_verification")
        if not isinstance(source_verification, dict):
            blockers.append(f"missing_tick_source_verification:{day_text}")
        else:
            primary_digest = str(
                source_verification.get("primary_content_sha256") or ""
            )
            verification_digest = str(
                source_verification.get(
                    "verification_content_sha256"
                ) or ""
            )
            try:
                primary_rows = int(
                    source_verification["primary_row_count"]
                )
                verification_rows = int(
                    source_verification["verification_row_count"]
                )
            except (KeyError, TypeError, ValueError):
                primary_rows = -1
                verification_rows = -2
            digests_valid = all(
                len(value) == 64
                and all(
                    character in "0123456789abcdef"
                    for character in value
                )
                for value in (primary_digest, verification_digest)
            )
            if not (
                source_verification.get("verified") is True
                and source_verification.get("method")
                == TICK_SOURCE_VERIFICATION
                and source_verification.get("content_digest")
                == TICK_CONTENT_DIGEST
                and source_verification.get("symbol") == symbol
                and primary_rows >= 0
                and primary_rows == verification_rows
                and digests_valid
                and primary_digest == verification_digest
                and not (source_verification.get("errors") or [])
            ):
                blockers.append(
                    f"invalid_tick_source_verification:{day_text}"
                )
        coverage = contract.get("coverage")
        if not isinstance(coverage, dict):
            blockers.append(f"missing_tick_coverage:{day_text}")
        else:
            complete_from = _utc(coverage.get("complete_from_utc"))
            complete_through = _utc(coverage.get("complete_through_utc"))
            try:
                row_count = int(coverage["row_count"])
            except (KeyError, TypeError, ValueError):
                row_count = -1
            if (
                complete_from is None
                or complete_through is None
                or complete_through < complete_from
                or row_count < 0
            ):
                blockers.append(f"invalid_tick_coverage:{day_text}")
            if coverage.get("coverage_source") == "legacy_parquet_bounds":
                blockers.append(f"legacy_tick_coverage:{day_text}")
        expected_hash = str(contract.get("parquet_sha256") or "")
        if len(expected_hash) != 64 or any(
            character not in "0123456789abcdef"
            for character in expected_hash
        ):
            blockers.append(f"invalid_parquet_fingerprint:{day_text}")
        return blockers

    @staticmethod
    def _filter_market_session(
        frame: pd.DataFrame,
        *,
        utc_offset_seconds: int,
    ) -> pd.DataFrame:
        server = frame["time_utc"] + pd.to_timedelta(
            utc_offset_seconds,
            unit="s",
        )
        weekday = server.dt.dayofweek
        second = (
            server.dt.hour * 3600
            + server.dt.minute * 60
            + server.dt.second
            + server.dt.microsecond / 1_000_000
        )
        open_second = 1 * 3600 + 1 * 60
        weekday_close = 23 * 3600 + 58 * 60
        friday_close = 23 * 3600 + 57 * 60
        tradable = (
            (
                weekday.between(0, 3)
                & second.ge(open_second)
                & second.lt(weekday_close)
            )
            | (
                weekday.eq(4)
                & second.ge(open_second)
                & second.lt(friday_close)
            )
        )
        return frame.loc[tradable].copy()

    def load_day(
        self,
        day: date,
    ) -> tuple[pd.DataFrame, dict | None, list[str]]:
        day_text = day.isoformat()
        if day_text in self._frames:
            return (
                self._frames[day_text],
                dict(self._evidence[day_text]),
                [],
            )
        parquet_path = self.cache_dir / f"{day_text}.parquet"
        contract_path = self.cache_dir / f"{day_text}.parquet.meta.json"
        missing = []
        if not parquet_path.is_file():
            missing.append(f"missing_tick_parquet:{day_text}")
        if not contract_path.is_file():
            missing.append(f"missing_tick_contract:{day_text}")
        if missing:
            return pd.DataFrame(), None, missing
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return pd.DataFrame(), None, [
                f"invalid_tick_contract_json:{day_text}"
            ]
        if not isinstance(contract, dict):
            return pd.DataFrame(), None, [
                f"invalid_tick_contract_json:{day_text}"
            ]
        blockers = self._contract_blockers(day_text, contract)
        observed_hash = _sha256_file(parquet_path)
        if observed_hash != contract.get("parquet_sha256"):
            blockers.append(f"parquet_hash_mismatch:{day_text}")
        if blockers:
            return pd.DataFrame(), None, list(dict.fromkeys(blockers))
        try:
            frame = pd.read_parquet(
                parquet_path,
                columns=["time_utc", "bid", "ask"],
            )
        except Exception as exc:
            return pd.DataFrame(), None, [
                f"tick_parquet_read_failed:{day_text}:{type(exc).__name__}"
            ]
        frame, frame_blockers = _normalise_ticks(frame)
        if frame_blockers:
            return pd.DataFrame(), None, [
                f"{blocker}:{day_text}" for blocker in frame_blockers
            ]
        if any(timestamp.date() != day for timestamp in frame["time_utc"]):
            return pd.DataFrame(), None, [
                f"tick_day_boundary_mismatch:{day_text}"
            ]
        source_verification = contract["source_verification"]
        if int(source_verification["primary_row_count"]) != len(frame):
            return pd.DataFrame(), None, [
                f"tick_source_row_count_mismatch:{day_text}"
            ]
        if (
            _ordered_tick_content_sha256(frame)
            != source_verification["primary_content_sha256"]
        ):
            return pd.DataFrame(), None, [
                f"tick_source_content_hash_mismatch:{day_text}"
            ]
        coverage = contract.get("coverage")
        if isinstance(coverage, dict):
            try:
                expected_rows = int(coverage["row_count"])
            except (KeyError, TypeError, ValueError):
                return pd.DataFrame(), None, [
                    f"invalid_tick_coverage:{day_text}"
                ]
            if expected_rows != len(frame):
                return pd.DataFrame(), None, [
                    f"tick_row_count_mismatch:{day_text}"
                ]
        raw_rows = len(frame)
        if self.require_market_session:
            frame = self._filter_market_session(
                frame,
                utc_offset_seconds=int(contract["utc_offset_seconds"]),
            )
            if frame.empty:
                return pd.DataFrame(), None, [
                    f"empty_market_session_ticks:{day_text}"
                ]
        frame = frame.reset_index(drop=True)
        evidence = {
            "day": day_text,
            "symbol": self.expected_symbol,
            "parquet_sha256": observed_hash,
            "contract_sha256": _sha256_file(contract_path),
            "row_count": raw_rows,
            "usable_row_count": len(frame),
            "first_tick_utc": (
                pd.Timestamp(frame.iloc[0]["time_utc"])
                .to_pydatetime()
                .isoformat()
            ),
            "last_tick_utc": (
                pd.Timestamp(frame.iloc[-1]["time_utc"])
                .to_pydatetime()
                .isoformat()
            ),
            "utc_offset_seconds": int(contract["utc_offset_seconds"]),
            "coverage": dict(contract["coverage"]),
            "source_verification": dict(contract["source_verification"]),
            "market_session_filtered": self.require_market_session,
        }
        self._frames[day_text] = frame
        self._evidence[day_text] = evidence
        return frame, dict(evidence), []

    def quote_loader(
        self,
        day: date,
    ) -> tuple[pd.DataFrame, str | None]:
        frame, _evidence, blockers = self.load_day(day)
        return frame, blockers[0] if blockers else None


def _counterfactual_horizon(
    opened: datetime,
    *,
    utc_offset_seconds: int,
) -> datetime | None:
    opened = opened.astimezone(timezone.utc)
    utc_day_end = opened.replace(
        hour=23,
        minute=59,
        second=59,
        microsecond=0,
    )
    server_time = opened + timedelta(seconds=utc_offset_seconds)
    weekday = server_time.weekday()
    if weekday > 4:
        return None
    close_second = (
        23 * 3600 + 57 * 60
        if weekday == 4
        else 23 * 3600 + 58 * 60
    )
    server_midnight = server_time.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    session_close = (
        server_midnight
        + timedelta(seconds=close_second - utc_offset_seconds)
    ).astimezone(timezone.utc)
    if session_close <= opened:
        return None
    return min(utc_day_end, session_close)


def counterfactual_horizon_blockers(
    *,
    trade: dict,
    market_tick_evidence: Iterable[dict],
    conversion_tick_evidence: Iterable[dict],
    require_conversion: bool,
) -> list[str]:
    """Prove that every altered path reaches its declared close horizon."""
    market_by_day = {
        str(item.get("day")): item
        for item in market_tick_evidence
        if isinstance(item, dict) and item.get("day")
    }
    conversion_by_day = {
        str(item.get("day")): item
        for item in conversion_tick_evidence
        if isinstance(item, dict) and item.get("day")
    }
    blockers: list[str] = []
    opened_values = {
        opened
        for ticket in trade.get("tickets") or []
        if (opened := _utc(ticket.get("open_dt_utc"))) is not None
    }
    trade_opened = _utc(trade.get("open_dt_utc"))
    if trade_opened is not None:
        opened_values.add(trade_opened)
    for opened in sorted(opened_values):
        day = opened.date().isoformat()
        market = market_by_day.get(day)
        if market is None:
            blockers.append(f"missing_market_policy_horizon:{day}")
            continue
        try:
            offset = int(market["utc_offset_seconds"])
        except (KeyError, TypeError, ValueError):
            blockers.append(f"invalid_market_policy_horizon:{day}")
            continue
        horizon = _counterfactual_horizon(
            opened,
            utc_offset_seconds=offset,
        )
        if horizon is None:
            blockers.append(f"invalid_market_policy_horizon:{day}")
            continue

        def covers(evidence: dict | None) -> bool:
            coverage = (evidence or {}).get("coverage")
            if not isinstance(coverage, dict):
                return False
            complete_from = _utc(coverage.get("complete_from_utc"))
            complete_through = _utc(coverage.get("complete_through_utc"))
            return bool(
                complete_from is not None
                and complete_through is not None
                and complete_from <= opened
                and complete_through >= horizon
            )

        if not covers(market):
            blockers.append(f"incomplete_market_policy_horizon:{day}")
        if require_conversion and not covers(conversion_by_day.get(day)):
            blockers.append(f"incomplete_conversion_policy_horizon:{day}")
    return list(dict.fromkeys(blockers))

def _normalise_events(
    events: Iterable[dict],
    *,
    kind: str,
    tick_size: float,
) -> tuple[list[dict], list[str]]:
    prepared: list[dict] = []
    blockers: list[str] = []
    for index, event in enumerate(events or []):
        timestamp = _utc(event.get("ts"))
        level = _finite_positive(event.get("level"))
        if timestamp is None:
            blockers.append(f"invalid_{kind}_event_time:{index}")
            continue
        if level is None:
            blockers.append(f"invalid_{kind}_event_level:{index}")
            continue
        prepared.append({
            "ts": timestamp,
            "level": level,
            "source": str(event.get("source") or ""),
            "source_index": index,
        })

    prepared.sort(key=lambda row: (row["ts"], row["source_index"]))
    deduplicated: list[dict] = []
    by_time: dict[datetime, list[dict]] = {}
    for event in prepared:
        by_time.setdefault(event["ts"], []).append(event)
    tolerance = tick_size / 10.0
    for timestamp, group in by_time.items():
        first = group[0]
        if any(
            abs(float(event["level"]) - float(first["level"])) > tolerance
            for event in group[1:]
        ):
            blockers.append(
                f"conflicting_{kind}_events:{timestamp.isoformat()}"
            )
            continue
        deduplicated.append(first)
    return deduplicated, blockers


def _normalise_ticks(ticks: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    required = {"time_utc", "bid", "ask"}
    missing = sorted(required - set(ticks.columns))
    if missing:
        return pd.DataFrame(), [
            f"missing_tick_column:{column}" for column in missing
        ]
    if ticks.empty:
        return ticks.copy(), ["missing_ticks"]

    frame = ticks.loc[:, ["time_utc", "bid", "ask"]].copy()
    frame["_source_index"] = range(len(frame))
    frame["time_utc"] = pd.to_datetime(
        frame["time_utc"],
        utc=True,
        errors="coerce",
    )
    frame["bid"] = pd.to_numeric(frame["bid"], errors="coerce")
    frame["ask"] = pd.to_numeric(frame["ask"], errors="coerce")

    invalid_time = frame["time_utc"].isna().to_numpy()
    bid_values = frame["bid"].to_numpy(dtype=float, copy=False)
    ask_values = frame["ask"].to_numpy(dtype=float, copy=False)
    invalid_quote = (
        ~np.isfinite(bid_values)
        | ~np.isfinite(ask_values)
        | (bid_values <= 0)
        | (ask_values <= 0)
    )
    crossed_quote = ~invalid_quote & (ask_values < bid_values)
    blockers: list[str] = []
    problem_indices = np.flatnonzero(
        invalid_time | invalid_quote | crossed_quote
    )
    for index in problem_indices:
        if invalid_time[index]:
            blockers.append(f"invalid_tick_time:{index}")
        if invalid_quote[index]:
            blockers.append(f"invalid_quote:{index}")
        elif crossed_quote[index]:
            blockers.append(f"crossed_quote:{index}")
    valid_times = frame["time_utc"].dropna()
    if not valid_times.is_monotonic_increasing:
        blockers.append("non_monotonic_ticks")
    return frame, list(dict.fromkeys(blockers))


def prepare_tick_window(
    ticks: pd.DataFrame | PreparedTickWindow,
) -> tuple[PreparedTickWindow | None, list[str]]:
    if isinstance(ticks, PreparedTickWindow):
        return ticks, []
    frame, blockers = _normalise_ticks(ticks)
    if blockers:
        return None, blockers
    return (
        PreparedTickWindow(
            frame=frame,
            times_ns=frame["time_utc"].array.as_unit("ns").asi8,
            bid=frame["bid"].to_numpy(dtype=float, copy=False),
            ask=frame["ask"].to_numpy(dtype=float, copy=False),
            source_indices=frame["_source_index"].to_numpy(
                dtype=np.int64,
                copy=False,
            ),
        ),
        [],
    )


def _active_event(events: list[dict], timestamp: datetime) -> dict | None:
    active = None
    for event in events:
        if event["ts"] > timestamp:
            break
        active = event
    return active


def _outcome(
    *,
    direction: str,
    side_price: float,
    sl: dict | None,
    tp: dict | None,
) -> tuple[str, float, float] | None:
    if sl is None or tp is None:
        return None
    if direction == "BUY":
        if side_price <= float(sl["level"]):
            return "sl", float(sl["level"]), side_price
        if side_price >= float(tp["level"]):
            return "tp", float(tp["level"]), float(tp["level"])
    else:
        if side_price >= float(sl["level"]):
            return "sl", float(sl["level"]), side_price
        if side_price <= float(tp["level"]):
            return "tp", float(tp["level"]), float(tp["level"])
    return None


def _same_outcome(
    outcomes: list[tuple[str, float, float]],
    *,
    tick_size: float,
) -> bool:
    if not outcomes:
        return True
    first = outcomes[0]
    tolerance = tick_size / 10.0
    return all(
        outcome[0] == first[0]
        and abs(outcome[1] - first[1]) <= tolerance
        and abs(outcome[2] - first[2]) <= tolerance
        for outcome in outcomes[1:]
    )


def replay_first_close(
    *,
    direction: str,
    opened_at: datetime,
    open_price: object,
    ticks: pd.DataFrame | PreparedTickWindow,
    sl_events: Iterable[dict],
    tp_events: Iterable[dict],
    horizon_at: datetime,
    tick_size: object,
    forced_close_at: datetime | None = None,
    allow_horizon_close: bool = True,
) -> dict:
    """Return the first independently provable close for one ticket."""
    direction = str(direction or "").upper()
    opened = _utc(opened_at)
    horizon = _utc(horizon_at)
    forced = _utc(forced_close_at) if forced_close_at is not None else None
    entry = _finite_positive(open_price)
    price_step = _finite_positive(tick_size)
    input_blockers: list[str] = []
    if direction not in {"BUY", "SELL"}:
        input_blockers.append("invalid_direction")
    if opened is None:
        input_blockers.append("invalid_open_time")
    if horizon is None:
        input_blockers.append("invalid_horizon_time")
    if entry is None:
        input_blockers.append("invalid_open_price")
    if price_step is None:
        input_blockers.append("invalid_tick_size")
    if opened is not None and horizon is not None and horizon < opened:
        input_blockers.append("horizon_before_open")
    if forced_close_at is not None and forced is None:
        input_blockers.append("invalid_forced_close_time")
    if input_blockers:
        return _blocked(*input_blockers)

    prepared_ticks, tick_blockers = prepare_tick_window(ticks)
    sl_rows, sl_blockers = _normalise_events(
        sl_events,
        kind="sl",
        tick_size=price_step,
    )
    tp_rows, tp_blockers = _normalise_events(
        tp_events,
        kind="tp",
        tick_size=price_step,
    )
    blockers = [*tick_blockers, *sl_blockers, *tp_blockers]
    if not sl_rows and not sl_blockers:
        blockers.append("missing_strategy_sl")
    if not tp_rows and not tp_blockers:
        blockers.append("missing_strategy_tp")
    if blockers:
        return _blocked(*blockers)
    if prepared_ticks is None:
        return _blocked("invalid_prepared_tick_window")
    frame = prepared_ticks.frame

    if any(
        (direction == "BUY" and event["level"] <= entry)
        or (direction == "SELL" and event["level"] >= entry)
        for event in tp_rows
    ):
        return _blocked("invalid_tp_direction")

    times_ns = prepared_ticks.times_ns
    opened_ns = pd.Timestamp(opened).value
    horizon_ns = pd.Timestamp(horizon).value
    window_start = int(np.searchsorted(times_ns, opened_ns, side="left"))
    window_stop = int(np.searchsorted(
        times_ns,
        horizon_ns,
        side="right",
    ))
    if window_start >= window_stop:
        return _blocked("missing_ticks_after_open")

    quote_side = "bid" if direction == "BUY" else "ask"
    bid_values = prepared_ticks.bid
    ask_values = prepared_ticks.ask
    side_values = bid_values if quote_side == "bid" else ask_values
    source_indices = prepared_ticks.source_indices
    tolerance = price_step / 10.0

    def timestamp_from_ns(value: int) -> datetime:
        return pd.Timestamp(
            value,
            unit="ns",
            tz="UTC",
        ).to_pydatetime()

    def group_bounds(index: int) -> tuple[int, int]:
        timestamp_ns = times_ns[index]
        return (
            max(
                window_start,
                int(np.searchsorted(
                    times_ns,
                    timestamp_ns,
                    side="left",
                )),
            ),
            min(
                window_stop,
                int(np.searchsorted(
                    times_ns,
                    timestamp_ns,
                    side="right",
                )),
            ),
        )

    def outcome_mask(
        start: int,
        stop: int,
        sl: dict | None,
        tp: dict | None,
    ) -> np.ndarray:
        if sl is None or tp is None:
            return np.zeros(stop - start, dtype=bool)
        prices = side_values[start:stop]
        if direction == "BUY":
            return (prices <= float(sl["level"])) | (
                prices >= float(tp["level"])
            )
        return (prices >= float(sl["level"])) | (
            prices <= float(tp["level"])
        )

    def touch_result(
        index: int,
        sl: dict,
        tp: dict,
    ) -> dict:
        group_start, group_stop = group_bounds(index)
        group_hits = outcome_mask(group_start, group_stop, sl, tp)
        hit_indices = group_start + np.flatnonzero(group_hits)
        outcomes = [
            _outcome(
                direction=direction,
                side_price=float(side_values[hit_index]),
                sl=sl,
                tp=tp,
            )
            for hit_index in hit_indices
        ]
        outcomes = [row for row in outcomes if row is not None]
        timestamp = timestamp_from_ns(int(times_ns[index]))
        if not _same_outcome(outcomes, tick_size=price_step):
            return _blocked(
                f"ambiguous_duplicate_tick_outcome:{timestamp.isoformat()}"
            )
        reason, level, close_price = outcomes[0]
        touch_index = int(hit_indices[0])
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "simulated",
            "blockers": [],
            "close_reason": reason,
            "close_time_utc": timestamp.isoformat(),
            "close_price": round(close_price, 8),
            "trigger_level": round(level, 8),
            "quote_side": quote_side,
            "touch_price": round(float(side_values[touch_index]), 8),
            "touch_bid": round(float(bid_values[touch_index]), 8),
            "touch_ask": round(float(ask_values[touch_index]), 8),
            "touch_source_index": int(source_indices[touch_index]),
        }

    def forced_result(index: int) -> dict:
        group_start, group_stop = group_bounds(index)
        prices = side_values[group_start:group_stop]
        timestamp = timestamp_from_ns(int(times_ns[index]))
        if np.any(np.abs(prices - prices[0]) > tolerance):
            return _blocked(
                f"ambiguous_management_quote:{timestamp.isoformat()}"
            )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "simulated",
            "blockers": [],
            "close_reason": "management_close",
            "close_time_utc": timestamp.isoformat(),
            "close_price": round(float(side_values[group_start]), 8),
            "trigger_level": None,
            "quote_side": quote_side,
            "touch_price": round(float(side_values[group_start]), 8),
            "touch_bid": round(float(bid_values[group_start]), 8),
            "touch_ask": round(float(ask_values[group_start]), 8),
            "touch_source_index": int(source_indices[group_start]),
        }

    forced_index = None
    if forced is not None:
        forced_ns = pd.Timestamp(forced).value
        candidate_index = int(np.searchsorted(
            times_ns,
            forced_ns,
            side="left",
        ))
        if candidate_index < window_start:
            candidate_index = window_start
        if candidate_index < window_stop:
            forced_index = candidate_index

    boundary_values = {opened_ns}
    boundary_values.update(
        pd.Timestamp(event["ts"]).value
        for event in (*sl_rows, *tp_rows)
        if opened < event["ts"] <= horizon
    )
    boundaries = sorted(boundary_values)
    for boundary_index, boundary_ns in enumerate(boundaries):
        segment_start = max(
            window_start,
            int(np.searchsorted(times_ns, boundary_ns, side="left")),
        )
        next_boundary = (
            boundaries[boundary_index + 1]
            if boundary_index + 1 < len(boundaries)
            else horizon_ns + 1
        )
        segment_stop = min(
            window_stop,
            int(np.searchsorted(
                times_ns,
                next_boundary,
                side="left",
            )),
        )
        if segment_start >= segment_stop:
            continue

        boundary_time = timestamp_from_ns(boundary_ns)
        sl = _active_event(sl_rows, boundary_time)
        tp = _active_event(tp_rows, boundary_time)
        if sl is not None and tp is not None:
            invalid_geometry = (
                direction == "BUY" and sl["level"] >= tp["level"]
            ) or (
                direction == "SELL" and sl["level"] <= tp["level"]
            )
            if invalid_geometry:
                timestamp = timestamp_from_ns(
                    int(times_ns[segment_start])
                )
                return _blocked(
                    f"invalid_active_level_geometry:{timestamp.isoformat()}"
                )

        hits = outcome_mask(segment_start, segment_stop, sl, tp)
        hit_positions = np.flatnonzero(hits)
        outcome_index = (
            segment_start + int(hit_positions[0])
            if len(hit_positions)
            else None
        )
        forced_in_segment = (
            forced_index is not None
            and segment_start <= forced_index < segment_stop
        )

        if outcome_index is not None and forced_in_segment:
            outcome_time = int(times_ns[outcome_index])
            forced_time = int(times_ns[forced_index])
            if outcome_time < forced_time:
                return touch_result(outcome_index, sl, tp)
            if forced_time < outcome_time:
                return forced_result(forced_index)

            group_start, group_stop = group_bounds(outcome_index)
            forced_prices = side_values[group_start:group_stop]
            outcome = _outcome(
                direction=direction,
                side_price=float(side_values[outcome_index]),
                sl=sl,
                tp=tp,
            )
            timestamp = timestamp_from_ns(outcome_time)
            if (
                np.any(
                    np.abs(forced_prices - forced_prices[0]) > tolerance
                )
                or outcome is None
                or abs(outcome[2] - forced_prices[0]) > tolerance
            ):
                return _blocked(
                    f"ambiguous_management_touch_order:"
                    f"{timestamp.isoformat()}"
                )
            return touch_result(outcome_index, sl, tp)
        if outcome_index is not None:
            return touch_result(outcome_index, sl, tp)
        if forced_in_segment:
            return forced_result(forced_index)

    if forced is not None:
        return _blocked("missing_ticks_after_management")
    if not allow_horizon_close:
        return _blocked("open_at_horizon")
    final_index = window_stop - 1
    group_start, group_stop = group_bounds(final_index)
    prices = side_values[group_start:group_stop]
    timestamp = timestamp_from_ns(int(times_ns[final_index]))
    if np.any(np.abs(prices - prices[0]) > tolerance):
        return _blocked(f"ambiguous_horizon_quote:{timestamp.isoformat()}")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "simulated",
        "blockers": [],
        "close_reason": "horizon_close",
        "close_time_utc": timestamp.isoformat(),
        "close_price": round(float(side_values[final_index]), 8),
        "trigger_level": None,
        "quote_side": quote_side,
        "touch_price": round(float(side_values[final_index]), 8),
        "touch_bid": round(float(bid_values[final_index]), 8),
        "touch_ask": round(float(ask_values[final_index]), 8),
        "touch_source_index": int(source_indices[final_index]),
    }


def _ticket_id(ticket: dict) -> str:
    value = ticket.get("ticket") or ticket.get("position_id")
    if value in (None, ""):
        return "unknown"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _actual_ticket_pnl(ticket: dict) -> float | None:
    value = ticket.get("pnl_net")
    if value is None:
        value = (ticket.get("pnl_components") or {}).get("net")
    number = _decimal(value)
    return float(number) if number is not None else None


def _is_be_source(source: object) -> bool:
    text = str(source or "").upper()
    return bool(
        "BREAKEVEN" in text
        or "BREAK_EVEN" in text
        or re.search(r"(^|[^A-Z])BE([^A-Z]|$)", text)
    )


def _confirmed_level_events(
    ticket: dict,
    *,
    key: str,
    tick_size: float,
    remove_be: bool,
) -> tuple[list[dict], list[str]]:
    label = _ticket_id(ticket)
    open_price = _finite_positive(ticket.get("open_price"))
    rows: list[dict] = []
    for event in ticket.get(f"{key}_history") or []:
        if event.get("status") not in {"confirmed", "snapshot"}:
            continue
        value = event.get(key)
        if remove_be and _is_be_source(event.get("source")):
            continue
        level = _finite_positive(value)
        if (
            remove_be
            and key == "sl"
            and level is not None
            and open_price is not None
            and abs(level - open_price) <= max(0.05, tick_size * 5)
        ):
            continue
        rows.append({
            "ts": event.get("ts"),
            "level": value,
            "source": event.get("source"),
            "status": event.get("status"),
        })
    by_timestamp: dict[datetime, list[dict]] = {}
    unresolved: list[dict] = []
    for row in rows:
        timestamp = _utc(row.get("ts"))
        if timestamp is None:
            unresolved.append(row)
            continue
        by_timestamp.setdefault(timestamp, []).append(row)
    authoritative = list(unresolved)
    authority = {"confirmed": 1, "snapshot": 2}
    for timestamp, group in by_timestamp.items():
        highest = max(
            authority.get(str(row.get("status")), 0)
            for row in group
        )
        authoritative.extend(
            {
                **row,
                "ts": timestamp,
            }
            for row in group
            if authority.get(str(row.get("status")), 0) == highest
        )
    prepared, blockers = _normalise_events(
        authoritative,
        kind=f"{key}:{label}",
        tick_size=tick_size,
    )
    return prepared, blockers


def _provider_trigger(
    provider_signal: dict | None,
    *,
    trigger_action: str,
) -> tuple[datetime | None, str | None, list[str]]:
    candidates: list[datetime] = []
    blockers: list[str] = []
    for index, event in enumerate(
        (provider_signal or {}).get("management_events") or []
    ):
        action = str(
            event.get("classified_action")
            or event.get("classified")
            or event.get("action")
            or ""
        )
        actions = {action}
        actions.update(
            str(option.get("action") or "")
            for option in event.get("execution_options") or []
            if isinstance(option, dict)
        )
        if trigger_action not in actions:
            continue
        timestamp = _utc(
            event.get("observed_ts_utc")
            or event.get("telegram_ts_utc")
        )
        if timestamp is None:
            blockers.append(f"invalid_provider_trigger_time:{index}")
        else:
            candidates.append(timestamp)
    if blockers:
        return None, None, blockers
    if not candidates:
        return None, None, []
    return min(candidates), "canonical_provider_management", []


def _confirmed_be_trigger(
    tickets: Iterable[dict],
    *,
    tick_size: float,
) -> datetime | None:
    candidates: list[datetime] = []
    for ticket in tickets:
        entry = _finite_positive(ticket.get("open_price"))
        for event in ticket.get("sl_history") or []:
            if event.get("status") not in {"confirmed", "snapshot"}:
                continue
            level = _finite_positive(event.get("sl"))
            timestamp = _utc(event.get("ts"))
            if level is None or timestamp is None:
                continue
            if _is_be_source(event.get("source")) or (
                entry is not None
                and abs(level - entry) <= max(0.05, tick_size * 5)
            ):
                candidates.append(timestamp)
    return min(candidates) if candidates else None


def _policy_allocation(policy: dict, leg_count: int) -> dict[str, int]:
    counts = {
        "close_now": int(policy.get("close_legs") or 0),
        "move_to_be": int(policy.get("be_legs") or 0),
        "runner": int(policy.get("runner_legs") or 0),
    }
    original = dict(counts)
    while sum(counts.values()) > leg_count:
        reducible = [
            key
            for key, value in counts.items()
            if value > 1 and original[key] > 0
        ]
        if reducible:
            key = max(
                reducible,
                key=lambda name: (
                    counts[name],
                    {"move_to_be": 2, "close_now": 1, "runner": 0}[name],
                ),
            )
        else:
            key = next(
                name
                for name in ("move_to_be", "close_now", "runner")
                if counts[name] > 0
            )
        counts[key] -= 1
    if sum(counts.values()) < leg_count:
        counts["runner"] += leg_count - sum(counts.values())
    return counts


def _active_tp_distance(
    *,
    direction: str,
    ticket: dict,
    trigger: datetime,
    tick_size: float,
) -> tuple[float | None, list[str]]:
    events, blockers = _confirmed_level_events(
        ticket,
        key="tp",
        tick_size=tick_size,
        remove_be=True,
    )
    if blockers:
        return None, blockers
    active = [event for event in events if event["ts"] <= trigger]
    if not active:
        return None, [f"missing_causal_tp_at_trigger:{_ticket_id(ticket)}"]
    entry = _finite_positive(ticket.get("open_price"))
    if entry is None:
        return None, [f"invalid_ticket_open_price:{_ticket_id(ticket)}"]
    target = float(active[-1]["level"])
    distance = target - entry if direction == "BUY" else entry - target
    if distance < 0:
        return None, [f"invalid_causal_tp_direction:{_ticket_id(ticket)}"]
    return distance, []


def _policy_blockers(policy: dict) -> list[str]:
    blockers: list[str] = []
    mode = policy.get("mode")
    if mode not in {"follow_actual", "risk_free_allocation"}:
        blockers.append("unsupported_policy_mode")
    if policy.get("entry_policy") != "actual_mt5":
        blockers.append("non_mt5_entry_policy")
    if policy.get("horizon_policy") != "eod_close":
        blockers.append("unsupported_horizon_policy")
    counts: list[int] = []
    for field in ("close_legs", "be_legs", "runner_legs", "base_leg_count"):
        value = policy.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            blockers.append(f"invalid_policy_{field}")
        else:
            counts.append(value)
    if len(counts) == 4 and sum(counts[:3]) != counts[3]:
        blockers.append("invalid_policy_allocation")
    if not policy.get("policy_id"):
        blockers.append("missing_policy_id")
    if not policy.get("trigger_action"):
        blockers.append("missing_policy_trigger_action")
    return list(dict.fromkeys(blockers))


def _unchanged_policy_result(
    *,
    trade: dict,
    tickets: list[dict],
    policy_id: str,
    leg_action: str,
) -> dict:
    actual_pnl = sum(_actual_ticket_pnl(ticket) or 0.0 for ticket in tickets)
    results = [
        {
            "ticket": ticket.get("ticket"),
            "status": "unchanged_no_strategy_event",
            "leg_action": leg_action,
            "open_time_utc": _utc(ticket["open_dt_utc"]).isoformat(),
            "open_price": float(ticket["open_price"]),
            "volume": float(ticket["volume"]),
            "close_reason": ticket.get("close_reason"),
            "close_time_utc": (
                _utc(ticket.get("close_dt_utc")).isoformat()
                if _utc(ticket.get("close_dt_utc")) is not None
                else None
            ),
            "close_price": ticket.get("close_price"),
            "strategy_pnl": _actual_ticket_pnl(ticket),
            "actual_pnl": _actual_ticket_pnl(ticket),
            "money_status": "mt5_actual",
            "blockers": [],
        }
        for ticket in tickets
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "sig_id": trade.get("sig_id"),
        "channel": trade.get("channel"),
        "direction": str(trade.get("direction") or "").upper(),
        "strategy": policy_id,
        "entry_authority": "mt5_deals",
        "status": "unchanged",
        "actual_pnl": round(actual_pnl, 2),
        "strategy_pnl": round(actual_pnl, 2),
        "delta_pnl": 0.0,
        "management_trigger_utc": None,
        "management_trigger_source": None,
        "blockers": [],
        "tickets": results,
    }


def replay_policy_trade(
    *,
    trade: dict,
    ticks: pd.DataFrame | PreparedTickWindow,
    policy: dict,
    provider_signal: dict | None,
    money_oracle: IndependentMoneyOracle,
    tick_size: object,
) -> dict:
    """Independently replay one declared policy over actual MT5 entries."""
    policy_id = str(policy.get("policy_id") or "")
    direction = str(trade.get("direction") or "").upper()
    price_step = _finite_positive(tick_size)
    blockers = _policy_blockers(policy)
    if direction not in {"BUY", "SELL"}:
        blockers.append("invalid_direction")
    if price_step is None:
        blockers.append("invalid_tick_size")
    prepared_ticks, tick_blockers = prepare_tick_window(ticks)
    blockers.extend(tick_blockers)

    tickets = list(trade.get("tickets") or [])
    if not tickets:
        blockers.append("missing_tickets")
    labels = [_ticket_id(ticket) for ticket in tickets]
    if "unknown" in labels or len(set(labels)) != len(labels):
        blockers.append("duplicate_or_missing_ticket_id")
    for ticket, label in zip(tickets, labels):
        if _utc(ticket.get("open_dt_utc")) is None:
            blockers.append(f"invalid_ticket_open_time:{label}")
        if _finite_positive(ticket.get("open_price")) is None:
            blockers.append(f"invalid_ticket_open_price:{label}")
        if _finite_positive(ticket.get("volume")) is None:
            blockers.append(f"invalid_ticket_volume:{label}")
        if _actual_ticket_pnl(ticket) is None:
            blockers.append(f"invalid_ticket_actual_pnl:{label}")
    if blockers:
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": trade.get("sig_id"),
            "strategy": policy_id,
            "status": "blocked",
            "strategy_pnl": None,
            "blockers": list(dict.fromkeys(blockers)),
            "tickets": [],
        }

    actual_pnl = sum(_actual_ticket_pnl(ticket) or 0.0 for ticket in tickets)
    if policy["mode"] == "follow_actual":
        return _unchanged_policy_result(
            trade=trade,
            tickets=tickets,
            policy_id=policy_id,
            leg_action="follow_actual",
        )

    trigger, trigger_source, trigger_blockers = _provider_trigger(
        provider_signal,
        trigger_action=str(policy["trigger_action"]),
    )
    if trigger_blockers:
        blockers.extend(trigger_blockers)
    if (
        trigger is None
        and policy["trigger_action"] == "MOVE_SL_TO_BE"
    ):
        confirmed_trigger = _confirmed_be_trigger(
            tickets,
            tick_size=price_step,
        )
        if confirmed_trigger is not None:
            trigger = confirmed_trigger
            trigger_source = "confirmed_mt5_level_history"
    open_times = [_utc(ticket["open_dt_utc"]) for ticket in tickets]
    if trigger is not None and trigger < min(open_times):
        blockers.append("management_trigger_before_trade_open")
    elif trigger is not None:
        blockers.extend(
            f"management_trigger_before_ticket_open:{label}"
            for label, opened in zip(labels, open_times)
            if trigger < opened
        )
    if blockers:
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": trade.get("sig_id"),
            "strategy": policy_id,
            "status": "blocked",
            "actual_pnl": round(actual_pnl, 2),
            "strategy_pnl": None,
            "management_trigger_utc": (
                trigger.isoformat() if trigger is not None else None
            ),
            "management_trigger_source": trigger_source,
            "blockers": list(dict.fromkeys(blockers)),
            "tickets": [],
        }
    if trigger is None:
        return _unchanged_policy_result(
            trade=trade,
            tickets=tickets,
            policy_id=policy_id,
            leg_action="unchanged_no_provider_trigger",
        )

    distances: dict[int, float] = {}
    for index, ticket in enumerate(tickets):
        distance, distance_blockers = _active_tp_distance(
            direction=direction,
            ticket=ticket,
            trigger=trigger,
            tick_size=price_step,
        )
        blockers.extend(distance_blockers)
        if distance is not None:
            distances[index] = distance
    if blockers:
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": trade.get("sig_id"),
            "strategy": policy_id,
            "status": "blocked",
            "actual_pnl": round(actual_pnl, 2),
            "strategy_pnl": None,
            "management_trigger_utc": trigger.isoformat(),
            "management_trigger_source": trigger_source,
            "blockers": list(dict.fromkeys(blockers)),
            "tickets": [],
        }

    ordered = sorted(
        enumerate(tickets),
        key=lambda item: (distances[item[0]], item[0]),
    )
    allocation = _policy_allocation(policy, len(tickets))
    actions = (
        ["close_now"] * allocation["close_now"]
        + ["move_to_be"] * allocation["move_to_be"]
        + ["runner"] * allocation["runner"]
    )
    action_by_index = {
        original_index: action
        for (original_index, _ticket), action in zip(ordered, actions)
    }

    results: list[dict] = []
    horizon = datetime.combine(
        open_times[0].date(),
        datetime.max.time().replace(microsecond=0),
        tzinfo=timezone.utc,
    )
    for index, ticket in enumerate(tickets):
        label = labels[index]
        action = action_by_index[index]
        sl_events, sl_blockers = _confirmed_level_events(
            ticket,
            key="sl",
            tick_size=price_step,
            remove_be=True,
        )
        tp_events, tp_blockers = _confirmed_level_events(
            ticket,
            key="tp",
            tick_size=price_step,
            remove_be=True,
        )
        if action == "move_to_be":
            sl_events = [
                *sl_events,
                {
                    "ts": trigger,
                    "level": float(ticket["open_price"]),
                    "source": "oracle_policy_be",
                    "source_index": len(sl_events),
                },
            ]
            sl_events.sort(key=lambda row: (row["ts"], row["source_index"]))
        close = replay_first_close(
            direction=direction,
            opened_at=open_times[index],
            open_price=ticket["open_price"],
            ticks=prepared_ticks,
            sl_events=sl_events,
            tp_events=tp_events,
            horizon_at=horizon,
            tick_size=price_step,
            forced_close_at=trigger if action == "close_now" else None,
        )
        ticket_blockers = [
            *sl_blockers,
            *tp_blockers,
            *(close.get("blockers") or []),
        ]
        if ticket_blockers:
            blockers.extend(
                f"{blocker}:{label}" for blocker in ticket_blockers
            )
            continue
        money = money_oracle.convert_leg(
            direction=direction,
            open_price=ticket["open_price"],
            close_price=close["close_price"],
            volume=ticket["volume"],
            open_time_utc=ticket["open_dt_utc"],
            close_time_utc=close["close_time_utc"],
        )
        if money["status"] != "verified":
            blockers.extend(
                f"{blocker}:{label}" for blocker in money["blockers"]
            )
            continue
        results.append({
            "ticket": ticket.get("ticket"),
            "status": "simulated",
            "leg_action": action,
            "open_time_utc": open_times[index].isoformat(),
            "open_price": float(ticket["open_price"]),
            "volume": float(ticket["volume"]),
            "actual_pnl": _actual_ticket_pnl(ticket),
            "strategy_pnl": money["strategy_pnl"],
            "close_reason": close["close_reason"],
            "close_time_utc": close["close_time_utc"],
            "close_price": close["close_price"],
            "trigger_level": close["trigger_level"],
            "touch_side": close["quote_side"],
            "touch_side_price": close["touch_price"],
            "touch_bid": close["touch_bid"],
            "touch_ask": close["touch_ask"],
            "touch_source_index": close["touch_source_index"],
            "money_status": money["status"],
            "pnl_currency": money["pnl_currency"],
            "profit_currency_pnl": money["profit_currency_pnl"],
            "money_conversion": money["conversion"],
            "money_formula": money.get("formula"),
            "blockers": [],
        })

    if blockers or len(results) != len(tickets):
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": trade.get("sig_id"),
            "strategy": policy_id,
            "status": "blocked",
            "actual_pnl": round(actual_pnl, 2),
            "strategy_pnl": None,
            "management_trigger_utc": trigger.isoformat(),
            "management_trigger_source": trigger_source,
            "blockers": list(dict.fromkeys(blockers)),
            "tickets": results,
        }
    strategy_pnl = sum(
        Decimal(str(ticket["strategy_pnl"])) for ticket in results
    ).quantize(money_oracle.quantum, rounding=ROUND_HALF_UP)
    return {
        "schema_version": SCHEMA_VERSION,
        "sig_id": trade.get("sig_id"),
        "channel": trade.get("channel"),
        "direction": direction,
        "strategy": policy_id,
        "entry_authority": "mt5_deals",
        "level_timeline_source": "execution_ticket_history",
        "status": "simulated",
        "actual_pnl": round(actual_pnl, 2),
        "strategy_pnl": float(strategy_pnl),
        "delta_pnl": round(float(strategy_pnl) - actual_pnl, 2),
        "management_trigger_utc": trigger.isoformat(),
        "management_trigger_source": trigger_source,
        "blockers": [],
        "tickets": results,
    }
