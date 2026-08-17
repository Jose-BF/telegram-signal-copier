"""Fail-closed Dubai Investing replay dataset construction."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol

import numpy as np
import pandas as pd


EXACT_AUDIT_STATUS = "exact"


class TickSource(Protocol):
    def load_day(
        self,
        day: date,
    ) -> tuple[pd.DataFrame, dict[str, Any] | None, list[str]]: ...


@dataclass(frozen=True)
class LevelEvent:
    observed_at: datetime
    level: float
    status: str
    source: str


@dataclass(frozen=True)
class ProviderEvent:
    observed_at: datetime
    action: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class DubaiLeg:
    ticket: str
    role: str
    volume: float
    opened_at: datetime
    open_price: float
    closed_at: datetime | None
    close_price: float | None
    close_reason: str | None
    actual_pnl_eur: Decimal
    tp_events: tuple[LevelEvent, ...]
    sl_events: tuple[LevelEvent, ...]


@dataclass(frozen=True)
class DubaiPath:
    signal_id: str
    day: str
    direction: str
    signal_observed_at: datetime
    opened_at: datetime
    actual_pnl_eur: Decimal
    legs: tuple[DubaiLeg, ...]
    provider_events: tuple[ProviderEvent, ...]
    times_ns: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    exit_quotes: np.ndarray
    fx_bid: np.ndarray
    fx_ask: np.ndarray
    fx_age_ms: np.ndarray
    fx_valid: np.ndarray
    contract_size: float
    conversion_orientation: str
    currency_digits: int
    market_evidence: tuple[Mapping[str, Any], ...]
    conversion_evidence: tuple[Mapping[str, Any], ...]

    @property
    def total_volume(self) -> float:
        return float(sum(leg.volume for leg in self.legs))


@dataclass(frozen=True)
class DubaiDataset:
    paths: tuple[DubaiPath, ...]
    exclusions: Mapping[str, tuple[str, ...]]
    source_hashes: Mapping[str, str]
    account_currency: str
    currency_digits: int

    @property
    def actual_pnl_eur(self) -> Decimal:
        quantum = Decimal(1).scaleb(-self.currency_digits)
        return sum(
            (path.actual_pnl_eur for path in self.paths),
            start=Decimal(0),
        ).quantize(quantum)


def load_dubai_dataset(
    *,
    replay_path: Path,
    audit_path: Path,
    market_ticks: TickSource,
    conversion_ticks: TickSource | None,
    money_contract: Mapping[str, Any],
    from_date: str | None = None,
    to_date: str | None = None,
    max_hold_minutes: int = 240,
) -> DubaiDataset:
    replay_path = Path(replay_path)
    audit_path = Path(audit_path)
    if max_hold_minutes <= 0:
        raise ValueError("max_hold_minutes must be positive")
    account = money_contract.get("account") or {}
    instrument = money_contract.get("instrument") or {}
    conversion = money_contract.get("conversion") or {}
    account_currency = str(account.get("currency") or "")
    currency_digits = account.get("currency_digits")
    if not account_currency:
        raise ValueError("money contract is missing account currency")
    if isinstance(currency_digits, bool) or not isinstance(currency_digits, int):
        raise ValueError("money contract has invalid currency digits")
    contract_size = _positive_float(instrument.get("contract_size"))
    if contract_size is None:
        raise ValueError("money contract has invalid contract size")
    orientation = str(conversion.get("orientation") or "")
    if orientation not in {
        "identity",
        "account_base_profit_quote",
        "profit_base_account_quote",
    }:
        raise ValueError("money contract has unsupported conversion orientation")

    audit_rows = {row.get("sig_id"): row for row in _read_jsonl(audit_path)}
    exclusions: dict[str, list[str]] = defaultdict(list)
    selected: list[dict[str, Any]] = []
    start = _parse_date(from_date, date.min)
    end = _parse_date(to_date, date.max)
    for trade in _read_jsonl(replay_path):
        if trade.get("channel") != "canal1":
            continue
        observed = _parse_datetime(trade.get("signal_dt_utc"))
        signal_id = str(trade.get("sig_id") or "")
        if not signal_id or observed is None:
            if signal_id:
                exclusions["invalid_signal_time"].append(signal_id)
            continue
        if not start <= observed.date() <= end:
            continue
        audit = audit_rows.get(signal_id) or {}
        status = str(audit.get("status") or "missing_audit")
        if status != EXACT_AUDIT_STATUS:
            exclusions[_reason_name(status)].append(signal_id)
            continue
        direction = str(trade.get("direction") or "").upper()
        if direction not in {"BUY", "SELL"}:
            exclusions["invalid_direction"].append(signal_id)
            continue
        tickets = [
            ticket
            for ticket in trade.get("tickets") or []
            if _positive_float(ticket.get("volume")) is not None
        ]
        if not tickets:
            exclusions["no_mt5_entry_tickets"].append(signal_id)
            continue
        selected.append(trade)

    paths: list[DubaiPath] = []
    market_cache: dict[date, tuple[pd.DataFrame, Mapping[str, Any]]] = {}
    conversion_cache: dict[date, tuple[pd.DataFrame, Mapping[str, Any]]] = {}
    for trade in sorted(
        selected,
        key=lambda item: (_parse_datetime(item.get("signal_dt_utc")) or datetime.min.replace(tzinfo=timezone.utc), str(item.get("sig_id"))),
    ):
        signal_id = str(trade["sig_id"])
        legs = _build_legs(trade)
        if not legs:
            exclusions["invalid_mt5_entry_tickets"].append(signal_id)
            continue
        signal_observed_at = _parse_datetime(trade["signal_dt_utc"])
        if signal_observed_at is None:
            exclusions["invalid_signal_time"].append(signal_id)
            continue
        opened_at = min(leg.opened_at for leg in legs)
        last_fill_at = max(leg.opened_at for leg in legs)
        path_started_at = min(signal_observed_at, opened_at)
        horizon = last_fill_at + timedelta(minutes=max_hold_minutes)
        actual_closes = [
            leg.closed_at for leg in legs if leg.closed_at is not None
        ]
        if actual_closes:
            horizon = max(horizon, max(actual_closes))
        market_frame, market_evidence, blockers = _load_tick_range(
            market_ticks,
            path_started_at,
            horizon,
            market_cache,
        )
        if blockers:
            exclusions[_reason_name(blockers[0])].append(signal_id)
            continue
        if market_frame.empty:
            exclusions["empty_tick_path"].append(signal_id)
            continue

        times_ns = _readonly_array(_series_ns(market_frame["time_utc"]))
        bid = _readonly_array(
            market_frame["bid"].to_numpy(dtype=float, copy=True)
        )
        ask = _readonly_array(
            market_frame["ask"].to_numpy(dtype=float, copy=True)
        )
        direction = str(trade["direction"]).upper()
        exit_quotes = bid if direction == "BUY" else ask

        conversion_evidence: list[Mapping[str, Any]] = []
        if orientation == "identity":
            fx_bid = _readonly_array(np.ones(len(market_frame), dtype=float))
            fx_ask = _readonly_array(np.ones(len(market_frame), dtype=float))
            fx_age_ms = _readonly_array(
                np.zeros(len(market_frame), dtype=float)
            )
            fx_valid = _readonly_array(
                np.ones(len(market_frame), dtype=bool)
            )
        else:
            if conversion_ticks is None:
                exclusions["missing_conversion_ticks"].append(signal_id)
                continue
            conversion_frame, conversion_evidence, blockers = _load_tick_range(
                conversion_ticks,
                opened_at - timedelta(minutes=1),
                horizon,
                conversion_cache,
            )
            if blockers:
                exclusions[_reason_name(blockers[0])].append(signal_id)
                continue
            aligned = _align_conversion(
                times_ns,
                conversion_frame,
                max_age_ms=int(conversion.get("max_quote_age_ms") or 0),
            )
            if aligned is None:
                exclusions["stale_or_missing_conversion_quote"].append(signal_id)
                continue
            fx_bid = _readonly_array(aligned[0])
            fx_ask = _readonly_array(aligned[1])
            fx_age_ms = _readonly_array(aligned[2])
            fx_valid = _readonly_array(aligned[3])

        actual = _decimal(trade.get("pnl_real_mt5"))
        if actual is None:
            actual = sum((leg.actual_pnl_eur for leg in legs), start=Decimal(0))
        paths.append(DubaiPath(
            signal_id=signal_id,
            day=(_parse_datetime(trade["signal_dt_utc"]) or opened_at).date().isoformat(),
            direction=direction,
            signal_observed_at=signal_observed_at,
            opened_at=opened_at,
            actual_pnl_eur=actual,
            legs=legs,
            provider_events=_build_provider_events(trade),
            times_ns=times_ns,
            bid=bid,
            ask=ask,
            exit_quotes=exit_quotes,
            fx_bid=fx_bid,
            fx_ask=fx_ask,
            fx_age_ms=fx_age_ms,
            fx_valid=fx_valid,
            contract_size=contract_size,
            conversion_orientation=orientation,
            currency_digits=currency_digits,
            market_evidence=tuple(market_evidence),
            conversion_evidence=tuple(conversion_evidence),
        ))

    return DubaiDataset(
        paths=tuple(paths),
        exclusions={
            reason: tuple(sorted(set(signal_ids)))
            for reason, signal_ids in sorted(exclusions.items())
        },
        source_hashes={
            "replay": _sha256_file(replay_path),
            "audit": _sha256_file(audit_path),
            "money_contract": _sha256_json(money_contract),
        },
        account_currency=account_currency,
        currency_digits=currency_digits,
    )


def _build_legs(trade: Mapping[str, Any]) -> tuple[DubaiLeg, ...]:
    legs: list[DubaiLeg] = []
    for index, ticket in enumerate(trade.get("tickets") or []):
        volume = _positive_float(ticket.get("volume"))
        open_price = _positive_float(ticket.get("open_price"))
        fill_event = ticket.get("fill_event") or {}
        opened_at = _parse_datetime(fill_event.get("ts")) or _parse_datetime(
            ticket.get("open_dt_utc")
        )
        if volume is None or open_price is None or opened_at is None:
            continue
        closed_at = _parse_datetime(ticket.get("close_dt_utc"))
        close_price = _positive_float(ticket.get("close_price"))
        close_reason = str(ticket.get("close_reason") or "") or None
        label = ticket.get("ticket") or ticket.get("position_ticket") or index
        legs.append(DubaiLeg(
            ticket=str(label),
            role=str(ticket.get("role") or "unknown"),
            volume=volume,
            opened_at=opened_at,
            open_price=open_price,
            closed_at=closed_at,
            close_price=close_price,
            close_reason=close_reason,
            actual_pnl_eur=_decimal(ticket.get("pnl_net")) or Decimal(0),
            tp_events=_level_events(ticket.get("tp_history") or [], "tp"),
            sl_events=_level_events(ticket.get("sl_history") or [], "sl"),
        ))
    return tuple(sorted(legs, key=lambda leg: (leg.opened_at, leg.ticket)))


def _level_events(rows: list[Mapping[str, Any]], key: str) -> tuple[LevelEvent, ...]:
    events: list[LevelEvent] = []
    seen: set[tuple[datetime, float, str, str]] = set()
    for row in rows:
        if row.get("status") not in {"requested", "confirmed", "snapshot"}:
            continue
        observed_at = _parse_datetime(row.get("ts"))
        level = _positive_float(row.get(key))
        if observed_at is None or level is None:
            continue
        status = str(row.get("status"))
        source = str(row.get("source") or "")
        identity = (observed_at, level, status, source)
        if identity in seen:
            continue
        seen.add(identity)
        events.append(LevelEvent(
            observed_at=observed_at,
            level=level,
            status=status,
            source=source,
        ))
    return tuple(sorted(events, key=lambda event: (event.observed_at, event.level)))


def _build_provider_events(trade: Mapping[str, Any]) -> tuple[ProviderEvent, ...]:
    events: list[ProviderEvent] = []
    seen: set[tuple[datetime, str, str]] = set()
    # `timeline` also contains the bot's later MT5 requests/results. Feeding
    # those back into a counterfactual strategy would leak the original bot's
    # decisions. `management` is the normalized causal Telegram layer.
    for row in trade.get("management") or []:
        if not isinstance(row, Mapping):
            continue
        observed_at = _parse_datetime(
            row.get("observed_ts_utc") or row.get("ts") or row.get("timestamp")
        )
        action = str(
            row.get("classified")
            or row.get("classified_action")
            or row.get("action")
            or ""
        ).upper()
        if observed_at is None or not action:
            continue
        payload = dict(row)
        payload_hash = _sha256_json(payload)
        identity = (observed_at, action, payload_hash)
        if identity in seen:
            continue
        seen.add(identity)
        events.append(ProviderEvent(observed_at, action, payload))
    return tuple(sorted(events, key=lambda event: (event.observed_at, event.action)))


def _load_tick_range(
    source: TickSource,
    start: datetime,
    end: datetime,
    cache: dict[date, tuple[pd.DataFrame, Mapping[str, Any]]],
) -> tuple[pd.DataFrame, list[Mapping[str, Any]], list[str]]:
    frames: list[pd.DataFrame] = []
    evidence: list[Mapping[str, Any]] = []
    current = start.date()
    while current <= end.date():
        if current not in cache:
            frame, day_evidence, blockers = source.load_day(current)
            if blockers:
                return pd.DataFrame(), evidence, blockers
            if day_evidence is None:
                return pd.DataFrame(), evidence, [f"missing_tick_evidence:{current}"]
            normalized = _normalize_ticks(frame)
            if normalized is None:
                return pd.DataFrame(), evidence, [f"invalid_tick_frame:{current}"]
            cache[current] = (normalized, dict(day_evidence))
        frame, day_evidence = cache[current]
        frames.append(frame)
        evidence.append(day_evidence)
        current += timedelta(days=1)
    combined = pd.concat(frames, ignore_index=True).sort_values(
        "time_utc", kind="stable"
    )
    mask = combined["time_utc"].between(start, end, inclusive="both")
    return combined.loc[mask].reset_index(drop=True), evidence, []


def _normalize_ticks(frame: pd.DataFrame) -> pd.DataFrame | None:
    if frame is None or frame.empty or not {"time_utc", "bid", "ask"}.issubset(frame):
        return None
    result = frame.loc[:, ["time_utc", "bid", "ask"]].copy()
    result["time_utc"] = pd.to_datetime(result["time_utc"], utc=True, errors="coerce")
    result["bid"] = pd.to_numeric(result["bid"], errors="coerce")
    result["ask"] = pd.to_numeric(result["ask"], errors="coerce")
    values = result[["bid", "ask"]].to_numpy(dtype=float)
    if (
        result["time_utc"].isna().any()
        or not np.isfinite(values).all()
        or (values <= 0).any()
        or (result["ask"] < result["bid"]).any()
    ):
        return None
    if not result["time_utc"].is_monotonic_increasing:
        return None
    return result.reset_index(drop=True)


def _align_conversion(
    path_times_ns: np.ndarray,
    frame: pd.DataFrame,
    *,
    max_age_ms: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    if frame.empty or max_age_ms <= 0:
        return None
    fx_times_ns = _series_ns(frame["time_utc"])
    indices = np.searchsorted(fx_times_ns, path_times_ns, side="right") - 1
    has_prior_quote = indices >= 0
    safe_indices = np.maximum(indices, 0)
    ages_ms = np.full(len(path_times_ns), np.inf, dtype=float)
    ages_ms[has_prior_quote] = (
        path_times_ns[has_prior_quote] - fx_times_ns[safe_indices[has_prior_quote]]
    ) / 1_000_000
    valid = has_prior_quote & (ages_ms <= max_age_ms)
    fx_bid = np.full(len(path_times_ns), np.nan, dtype=float)
    fx_ask = np.full(len(path_times_ns), np.nan, dtype=float)
    bid_values = frame["bid"].to_numpy(dtype=float, copy=False)
    ask_values = frame["ask"].to_numpy(dtype=float, copy=False)
    fx_bid[has_prior_quote] = bid_values[safe_indices[has_prior_quote]]
    fx_ask[has_prior_quote] = ask_values[safe_indices[has_prior_quote]]
    return (
        fx_bid,
        fx_ask,
        ages_ms,
        valid,
    )


def _series_ns(values: pd.Series) -> np.ndarray:
    return values.array.as_unit("ns").asi8.copy()


def _readonly_array(values: np.ndarray) -> np.ndarray:
    result = np.ascontiguousarray(values)
    result.setflags(write=False)
    return result


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _parse_date(value: str | None, default: date) -> date:
    if value is None:
        return default
    return date.fromisoformat(value)


def _positive_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) and number > 0 else None


def _decimal(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"non-object JSONL row at {path}:{line_number}")
            rows.append(row)
    return rows


def _reason_name(value: str) -> str:
    return str(value or "unknown").split(":", 1)[0]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
