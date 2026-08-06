"""Exact account-currency conversion for provider-first strategy replay."""

from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from tools import ensure_replay_tick_cache


SUPPORTED_SCHEMA_VERSIONS = {1, 2}
SUPPORTED_CALC_MODES = {4}
SWAP_MODEL_INTRADAY = "intraday_only_zero"
SWAP_MODEL_POINTS = "mt5_points_rollover_v1"
SUPPORTED_SWAP_MODE_POINTS = 1
WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
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


def _canonical_sha256(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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

    if contract.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
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
    swap_model = costs.get("swap_model")
    if swap_model not in {SWAP_MODEL_INTRADAY, SWAP_MODEL_POINTS}:
        blockers.append("unsupported_swap_model")
    if swap_model == SWAP_MODEL_POINTS:
        account_server = str(account.get("server") or "")
        account_fingerprint = str(account.get("fingerprint") or "")
        instrument_symbol = str(instrument.get("symbol") or "")
        if not account_server:
            blockers.append("missing_account_server")
        if len(account_fingerprint) != 64 or any(
            character not in "0123456789abcdef"
            for character in account_fingerprint.lower()
        ):
            blockers.append("invalid_account_fingerprint")
        if costs.get("rollover_clock") != "broker_midnight":
            blockers.append("unsupported_swap_rollover_clock")
        bracket_seconds = costs.get("snapshot_bracket_max_seconds")
        if (
            not isinstance(bracket_seconds, int)
            or bracket_seconds <= 0
            or bracket_seconds > 24 * 3600
        ):
            blockers.append("invalid_swap_snapshot_bracket")
        zero_bracket_seconds = costs.get(
            "zero_multiplier_bracket_max_seconds"
        )
        if (
            not isinstance(zero_bracket_seconds, int)
            or zero_bracket_seconds <= 0
            or zero_bracket_seconds > 7 * 24 * 3600
        ):
            blockers.append("invalid_zero_multiplier_snapshot_bracket")
        snapshots = contract.get("swap_snapshots")
        if not isinstance(snapshots, list) or not snapshots:
            blockers.append("missing_swap_snapshots")
        else:
            for index, snapshot in enumerate(snapshots):
                blockers.extend(
                    _validate_swap_snapshot(
                        snapshot,
                        index=index,
                        account_server=account_server,
                        account_fingerprint=account_fingerprint,
                        instrument_symbol=instrument_symbol,
                    )
                )
    if live_validation.get("valid") is not True:
        blockers.append("live_tick_value_validation_failed")
    return _stable_strings(blockers)


def _validate_swap_snapshot(
    snapshot: object,
    *,
    index: int,
    account_server: str,
    account_fingerprint: str,
    instrument_symbol: str,
) -> list[str]:
    label = f"swap_snapshot_{index}"
    if not isinstance(snapshot, dict):
        return [f"{label}_invalid"]
    blockers: list[str] = []
    if snapshot.get("account_server") != account_server:
        blockers.append(f"{label}_account_server_mismatch")
    if snapshot.get("account_fingerprint") != account_fingerprint:
        blockers.append(f"{label}_account_fingerprint_mismatch")
    if snapshot.get("instrument_symbol") != instrument_symbol:
        blockers.append(f"{label}_instrument_symbol_mismatch")
    captured = _parse_utc(snapshot.get("captured_at_utc"))
    if captured is None:
        blockers.append(f"{label}_captured_at_invalid")
    time_evidence = snapshot.get("time_evidence") or {}
    evidence_hash = str(time_evidence.get("evidence_sha256") or "")
    if time_evidence.get("source") != "mql5_service_v1":
        blockers.append(f"{label}_native_evidence_missing")
    if len(evidence_hash) != 64 or any(
        character not in "0123456789abcdef"
        for character in evidence_hash.lower()
    ):
        blockers.append(f"{label}_native_evidence_hash_invalid")
    offset = time_evidence.get("utc_offset_seconds")
    if (
        not isinstance(offset, int)
        or abs(offset) > 14 * 3600
        or offset % 900 != 0
    ):
        blockers.append(f"{label}_utc_offset_invalid")
    specification = snapshot.get("specification")
    if not isinstance(specification, dict):
        blockers.append(f"{label}_specification_invalid")
        return blockers
    if (
        snapshot.get("specification_sha256")
        != _canonical_sha256(specification)
    ):
        blockers.append(f"{label}_specification_hash_mismatch")
    if specification.get("swap_mode") != SUPPORTED_SWAP_MODE_POINTS:
        blockers.append(f"{label}_swap_mode_unsupported")
    for key in ("swap_long", "swap_short"):
        if _decimal(specification.get(key)) is None:
            blockers.append(f"{label}_{key}_invalid")
    for key in ("point", "contract_size"):
        value = _decimal(specification.get(key))
        if value is None or value <= 0:
            blockers.append(f"{label}_{key}_invalid")
    if not specification.get("currency_profit"):
        blockers.append(f"{label}_currency_profit_missing")
    multipliers = specification.get("weekday_multipliers")
    if not isinstance(multipliers, dict):
        blockers.append(f"{label}_weekday_multipliers_invalid")
    else:
        for weekday in WEEKDAY_NAMES:
            value = _decimal(multipliers.get(weekday))
            if value is None or value < 0:
                blockers.append(
                    f"{label}_weekday_multiplier_{weekday}_invalid"
                )
    return blockers


class VerifiedConversionTickCache:
    def __init__(self, cache_dir: Path, *, symbol: str):
        self.cache_dir = Path(cache_dir)
        self.symbol = str(symbol)
        self._frames: dict[str, pd.DataFrame] = {}
        self.evidence_by_day: dict[str, dict] = {}

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
        self.evidence_by_day[day_text] = deepcopy(contract)
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
        self.costs = self.contract["costs"]
        self.swap_model = str(self.costs["swap_model"])
        self.swap_snapshots = sorted(
            list(self.contract.get("swap_snapshots") or []),
            key=lambda row: str(row.get("captured_at_utc") or ""),
        )
        self.currency = str(self.account["currency"])
        self.currency_digits = int(self.account["currency_digits"])
        self.quantum = Decimal(1).scaleb(-self.currency_digits)
        self._conversion_tick_cache: VerifiedConversionTickCache | None = None
        if quote_loader is None:
            if tick_cache_dir is None:
                raise ValueError("money conversion tick cache is required")
            cache = VerifiedConversionTickCache(
                tick_cache_dir,
                symbol=str(self.conversion.get("symbol") or ""),
            )
            self._conversion_tick_cache = cache
            quote_loader = cache.load_day
        self.quote_loader = quote_loader

    @property
    def conversion_tick_evidence(self) -> dict[str, dict]:
        if self._conversion_tick_cache is None:
            return {}
        return deepcopy(self._conversion_tick_cache.evidence_by_day)

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

    def _convert_amount_at(
        self,
        at_utc: datetime,
        profit_currency_pnl: Decimal,
    ) -> tuple[Decimal | None, dict | None, list[str]]:
        if profit_currency_pnl == 0:
            return Decimal("0"), {
                "symbol": self.conversion.get("symbol"),
                "side": "not_required_zero",
                "price": None,
                "time_utc": at_utc.isoformat(),
                "age_ms": 0,
                "freshness": "not_required_zero",
            }, []
        conversion, blockers = self._quote_at(at_utc, profit_currency_pnl)
        if blockers or conversion is None:
            return None, None, blockers
        quote = _decimal(conversion["price"])
        if quote is None or quote <= 0:
            return None, None, ["invalid_money_conversion_quote"]
        orientation = self.conversion["orientation"]
        if orientation == "account_base_profit_quote":
            account_pnl = profit_currency_pnl / quote
        elif orientation == "profit_base_account_quote":
            account_pnl = profit_currency_pnl * quote
        else:
            account_pnl = profit_currency_pnl
        return self._money(account_pnl), conversion, []

    def _points_swap(
        self,
        *,
        direction: str,
        volume: Decimal,
        opened: datetime,
        closed: datetime,
        verified_utc_offset_seconds: int | None = None,
    ) -> dict:
        if (
            isinstance(verified_utc_offset_seconds, bool)
            or (
                verified_utc_offset_seconds is not None
                and (
                    not isinstance(verified_utc_offset_seconds, int)
                    or abs(verified_utc_offset_seconds) > 14 * 3600
                )
            )
        ):
            return {
                "status": "blocked",
                "strategy_pnl": None,
                "profit_currency_pnl": None,
                "rollovers": [],
                "blockers": ["invalid_verified_swap_offset_evidence"],
            }
        window_start = opened - timedelta(days=1)
        window_end = closed + timedelta(days=1)
        relevant = [
            snapshot
            for snapshot in self.swap_snapshots
            if (
                (captured := _parse_utc(snapshot.get("captured_at_utc")))
                is not None
                and window_start <= captured <= window_end
            )
        ]
        offsets = {
            int((snapshot.get("time_evidence") or {})["utc_offset_seconds"])
            for snapshot in relevant
        }
        if not offsets and verified_utc_offset_seconds is None:
            return {
                "status": "blocked",
                "strategy_pnl": None,
                "profit_currency_pnl": None,
                "rollovers": [],
                "blockers": ["missing_swap_offset_evidence"],
            }
        if len(offsets) > 1:
            return {
                "status": "blocked",
                "strategy_pnl": None,
                "profit_currency_pnl": None,
                "rollovers": [],
                "blockers": ["swap_offset_transition_unverified"],
            }

        if offsets:
            offset_seconds = next(iter(offsets))
            if (
                verified_utc_offset_seconds is not None
                and verified_utc_offset_seconds != offset_seconds
            ):
                return {
                    "status": "blocked",
                    "strategy_pnl": None,
                    "profit_currency_pnl": None,
                    "rollovers": [],
                    "blockers": ["swap_offset_evidence_mismatch"],
                }
            offset_evidence = (
                "swap_snapshots_and_tick_contract"
                if verified_utc_offset_seconds is not None
                else "swap_snapshots"
            )
        else:
            offset_seconds = int(verified_utc_offset_seconds)
            offset_evidence = "verified_tick_contract"
        offset = timedelta(seconds=offset_seconds)
        server_opened = opened + offset
        server_midnight = datetime.combine(
            server_opened.date() + timedelta(days=1),
            time.min,
            tzinfo=timezone.utc,
        )
        bracket_seconds = int(
            self.costs["snapshot_bracket_max_seconds"]
        )
        zero_bracket_seconds = int(
            self.costs["zero_multiplier_bracket_max_seconds"]
        )
        rollovers: list[dict] = []
        total_profit_currency = Decimal("0")
        total_account = Decimal("0")

        while True:
            rollover_utc = server_midnight - offset
            if rollover_utc > closed:
                break
            label = rollover_utc.isoformat()
            server_day = (server_midnight.date() - timedelta(days=1))
            weekday = WEEKDAY_NAMES[server_day.weekday()]
            before = [
                snapshot
                for snapshot in relevant
                if (
                    (captured := _parse_utc(
                        snapshot.get("captured_at_utc")
                    )) is not None
                    and captured < rollover_utc
                    and (rollover_utc - captured).total_seconds()
                    <= bracket_seconds
                    and int(
                        (snapshot.get("time_evidence") or {}).get(
                            "utc_offset_seconds"
                        )
                    ) == offset_seconds
                )
            ]
            after = [
                snapshot
                for snapshot in relevant
                if (
                    (captured := _parse_utc(
                        snapshot.get("captured_at_utc")
                    )) is not None
                    and captured > rollover_utc
                    and (captured - rollover_utc).total_seconds()
                    <= bracket_seconds
                    and int(
                        (snapshot.get("time_evidence") or {}).get(
                            "utc_offset_seconds"
                        )
                    ) == offset_seconds
                )
            ]
            evidence_mode = "rollover_window"
            if not before or not after:
                if weekday not in {"saturday", "sunday"}:
                    return {
                        "status": "blocked",
                        "strategy_pnl": None,
                        "profit_currency_pnl": None,
                        "rollovers": rollovers,
                        "blockers": [
                            f"missing_swap_rollover_bracket:{label}"
                        ],
                    }
                closure_before = [
                    snapshot
                    for snapshot in relevant
                    if (
                        (captured := _parse_utc(
                            snapshot.get("captured_at_utc")
                        )) is not None
                        and captured < rollover_utc
                        and (rollover_utc - captured).total_seconds()
                        <= zero_bracket_seconds
                        and int(
                            (snapshot.get("time_evidence") or {}).get(
                                "utc_offset_seconds"
                            )
                        ) == offset_seconds
                    )
                ]
                closure_after = [
                    snapshot
                    for snapshot in relevant
                    if (
                        (captured := _parse_utc(
                            snapshot.get("captured_at_utc")
                        )) is not None
                        and captured > rollover_utc
                        and (captured - rollover_utc).total_seconds()
                        <= zero_bracket_seconds
                        and int(
                            (snapshot.get("time_evidence") or {}).get(
                                "utc_offset_seconds"
                            )
                        ) == offset_seconds
                    )
                ]
                if not closure_before or not closure_after:
                    return {
                        "status": "blocked",
                        "strategy_pnl": None,
                        "profit_currency_pnl": None,
                        "rollovers": rollovers,
                        "blockers": [
                            f"missing_swap_rollover_bracket:{label}"
                        ],
                    }
                pre = max(
                    closure_before,
                    key=lambda row: str(row["captured_at_utc"]),
                )
                post = min(
                    closure_after,
                    key=lambda row: str(row["captured_at_utc"]),
                )
                if (
                    pre.get("specification_sha256")
                    != post.get("specification_sha256")
                ):
                    return {
                        "status": "blocked",
                        "strategy_pnl": None,
                        "profit_currency_pnl": None,
                        "rollovers": rollovers,
                        "blockers": [
                            "swap_spec_changed_across_market_closure:"
                            f"{label}"
                        ],
                    }
                closure_multiplier = _decimal(
                    pre["specification"]["weekday_multipliers"][weekday]
                )
                if closure_multiplier != 0:
                    return {
                        "status": "blocked",
                        "strategy_pnl": None,
                        "profit_currency_pnl": None,
                        "rollovers": rollovers,
                        "blockers": [
                            f"missing_swap_rollover_bracket:{label}"
                        ],
                    }
                evidence_mode = "market_closure"
            else:
                pre = max(
                    before,
                    key=lambda row: str(row["captured_at_utc"]),
                )
                post = min(
                    after,
                    key=lambda row: str(row["captured_at_utc"]),
                )
            if (
                pre.get("specification_sha256")
                != post.get("specification_sha256")
            ):
                return {
                    "status": "blocked",
                    "strategy_pnl": None,
                    "profit_currency_pnl": None,
                    "rollovers": rollovers,
                    "blockers": [
                        f"swap_spec_changed_at_rollover:{label}"
                    ],
                }

            specification = pre["specification"]
            if (
                _decimal(specification.get("contract_size"))
                != _decimal(self.instrument.get("contract_size"))
                or str(specification.get("currency_profit"))
                != str(self.instrument.get("currency_profit"))
            ):
                return {
                    "status": "blocked",
                    "strategy_pnl": None,
                    "profit_currency_pnl": None,
                    "rollovers": rollovers,
                    "blockers": [
                        f"swap_instrument_contract_mismatch:{label}"
                    ],
                }
            multiplier = _decimal(
                specification["weekday_multipliers"][weekday]
            )
            rate = _decimal(
                specification[
                    "swap_long" if direction == "BUY" else "swap_short"
                ]
            )
            point = _decimal(specification["point"])
            contract_size = _decimal(specification["contract_size"])
            swap_profit_currency = (
                rate * multiplier * point * contract_size * volume
            )
            account_swap, conversion, conversion_blockers = (
                self._convert_amount_at(
                    rollover_utc,
                    swap_profit_currency,
                )
            )
            if conversion_blockers or account_swap is None:
                return {
                    "status": "blocked",
                    "strategy_pnl": None,
                    "profit_currency_pnl": None,
                    "rollovers": rollovers,
                    "blockers": conversion_blockers,
                }
            total_profit_currency += swap_profit_currency
            total_account += account_swap
            rollovers.append({
                "rollover_utc": label,
                "server_day": weekday,
                "multiplier": float(multiplier),
                "rate": float(rate),
                "profit_currency_pnl": float(
                    swap_profit_currency.quantize(
                        Decimal("0.00000001"),
                        rounding=ROUND_HALF_UP,
                    )
                ),
                "strategy_pnl": float(account_swap),
                "conversion": conversion,
                "pre_snapshot_utc": pre["captured_at_utc"],
                "post_snapshot_utc": post["captured_at_utc"],
                "specification_sha256": pre["specification_sha256"],
                "evidence_mode": evidence_mode,
            })
            server_midnight += timedelta(days=1)

        return {
            "status": "verified",
            "strategy_pnl": float(self._money(total_account)),
            "profit_currency_pnl": float(
                total_profit_currency.quantize(
                    Decimal("0.00000001"),
                    rounding=ROUND_HALF_UP,
                )
            ),
            "rollovers": rollovers,
            "offset_evidence": offset_evidence,
            "blockers": [],
        }

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
        verified_utc_offset_seconds: int | None = None,
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
            if (
                self.swap_model == SWAP_MODEL_INTRADAY
                and closed.date() != opened.date()
                and not allow_overnight
            ):
                blockers.append("overnight_cost_model_unverified")
        if blockers:
            return {
                "status": "blocked",
                "strategy_pnl": None,
                "price_strategy_pnl": None,
                "swap_strategy_pnl": None,
                "pnl_currency": self.currency,
                "profit_currency_pnl": None,
                "conversion": None,
                "swap": None,
                "blockers": _stable_strings(blockers),
            }

        price_delta = (
            close_value - open_value
            if direction == "BUY"
            else open_value - close_value
        )
        profit_currency_pnl = price_delta * contract_size * volume_value
        account_price, conversion, quote_blockers = self._convert_amount_at(
            closed,
            profit_currency_pnl,
        )
        if quote_blockers or account_price is None:
            return {
                "status": "blocked",
                "strategy_pnl": None,
                "price_strategy_pnl": None,
                "swap_strategy_pnl": None,
                "pnl_currency": self.currency,
                "profit_currency_pnl": float(profit_currency_pnl),
                "conversion": None,
                "swap": None,
                "blockers": quote_blockers,
            }

        if self.swap_model == SWAP_MODEL_POINTS:
            swap = self._points_swap(
                direction=direction,
                volume=volume_value,
                opened=opened,
                closed=closed,
                verified_utc_offset_seconds=verified_utc_offset_seconds,
            )
        elif closed.date() != opened.date():
            swap = {
                "status": (
                    "observed_required"
                    if allow_overnight
                    else "blocked"
                ),
                "strategy_pnl": 0.0 if allow_overnight else None,
                "profit_currency_pnl": None,
                "rollovers": [],
                "blockers": (
                    []
                    if allow_overnight
                    else ["overnight_cost_model_unverified"]
                ),
            }
        else:
            swap = {
                "status": "not_applicable_intraday",
                "strategy_pnl": 0.0,
                "profit_currency_pnl": 0.0,
                "rollovers": [],
                "blockers": [],
            }
        if swap["blockers"]:
            return {
                "status": "blocked",
                "strategy_pnl": None,
                "price_strategy_pnl": float(account_price),
                "swap_strategy_pnl": None,
                "pnl_currency": self.currency,
                "profit_currency_pnl": float(profit_currency_pnl),
                "conversion": conversion,
                "swap": swap,
                "blockers": swap["blockers"],
            }

        account_swap = _decimal(swap["strategy_pnl"]) or Decimal("0")
        account_total = self._money(account_price + account_swap)
        formula = {
            "directional_delta": float(price_delta),
            "contract_size": float(contract_size),
            "volume": float(volume_value),
        }
        if profit_currency_pnl != 0:
            formula.update({
                "orientation": self.conversion["orientation"],
                "rounding": "ROUND_HALF_UP",
                "currency_digits": self.currency_digits,
            })
        return {
            "status": "verified",
            "strategy_pnl": float(account_total),
            "price_strategy_pnl": float(account_price),
            "swap_strategy_pnl": float(account_swap),
            "pnl_currency": self.currency,
            "profit_currency_pnl": float(
                profit_currency_pnl.quantize(
                    Decimal("0.00000001"),
                    rounding=ROUND_HALF_UP,
                )
            ),
            "conversion": conversion,
            "swap": swap,
            "formula": formula,
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
            observed_swap = (
                _decimal(components.get("swap")) or Decimal("0")
            ).quantize(
                converter.quantum,
                rounding=ROUND_HALF_UP,
            )
            modeled_swap = (
                (money.get("swap") or {}).get("status") == "verified"
            )
            expected_swap = (
                _decimal(money.get("swap_strategy_pnl"))
                if modeled_swap
                else observed_swap
            )
            expected_price = _decimal(
                money.get("price_strategy_pnl")
            )
            if expected_price is None:
                expected_price = _decimal(money.get("strategy_pnl"))
            expected = None
            if expected_price is not None and expected_swap is not None:
                expected = (expected_price + expected_swap).quantize(
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
            swap_difference = (
                None
                if expected_swap is None
                else (
                    expected_swap.quantize(
                        converter.quantum,
                        rounding=ROUND_HALF_UP,
                    )
                    - observed_swap
                ).quantize(
                    converter.quantum,
                    rounding=ROUND_HALF_UP,
                )
            )
            observed_price = _decimal(components.get("profit"))
            price_difference = (
                None
                if observed_price is None or expected_price is None
                else (
                    expected_price.quantize(
                        converter.quantum,
                        rounding=ROUND_HALF_UP,
                    )
                    - observed_price.quantize(
                        converter.quantum,
                        rounding=ROUND_HALF_UP,
                    )
                ).quantize(
                    converter.quantum,
                    rounding=ROUND_HALF_UP,
                )
            )
            exact_components = (
                (swap_difference is None or swap_difference == 0)
                and (price_difference is None or price_difference == 0)
            )
            rows.append({
                "sig_id": trade.get("sig_id"),
                "ticket": ticket.get("ticket"),
                "status": (
                    "exact"
                    if difference == 0 and exact_components
                    else "mismatch"
                ),
                "expected": float(expected),
                "observed": float(observed),
                "difference": float(difference),
                "expected_price": (
                    None
                    if expected_price is None
                    else float(expected_price)
                ),
                "observed_price": (
                    None
                    if observed_price is None
                    else float(observed_price)
                ),
                "price_difference": (
                    None
                    if price_difference is None
                    else float(price_difference)
                ),
                "expected_swap": (
                    None
                    if expected_swap is None
                    else float(expected_swap)
                ),
                "observed_swap": float(observed_swap),
                "swap_difference": (
                    None
                    if swap_difference is None
                    else float(swap_difference)
                ),
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

