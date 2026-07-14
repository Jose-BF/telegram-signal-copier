from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from numbers import Real

import numpy as np
import pandas as pd
from pandas.api.types import (
    is_datetime64_any_dtype,
    is_float_dtype,
    is_integer_dtype,
)

from provider_trade_spec import ProviderTradeSpec


_REQUIRED_TICK_COLUMNS = ("time_utc", "bid", "ask")


@dataclass(frozen=True)
class VirtualEntry:
    status: str
    time_utc: datetime | None
    price: float | None
    side: str | None
    latency_ms: int
    blockers: tuple[str, ...]


def _blocked(spec: ProviderTradeSpec, *blockers: str) -> VirtualEntry:
    return VirtualEntry(
        status="blocked",
        time_utc=None,
        price=None,
        side=None,
        latency_ms=spec.latency_ms,
        blockers=tuple(blockers),
    )


def _entry_trigger_utc(spec: ProviderTradeSpec) -> tuple[datetime | None, str | None]:
    trigger = spec.trigger_observed_utc
    if trigger is None:
        return None, "missing_trigger_observed_utc"
    try:
        if trigger.tzinfo is None or trigger.utcoffset() is None:
            return None, "invalid_trigger_observed_utc"
        return trigger.astimezone(timezone.utc), None
    except (OverflowError, ValueError):
        return None, "invalid_trigger_observed_utc"


def _is_supported_tick_time(value: object) -> bool:
    if isinstance(value, (pd.Timestamp, datetime)):
        return not pd.isna(value)
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _normalise_tick_times(values: pd.Series) -> pd.Series | None:
    if not is_datetime64_any_dtype(values.dtype):
        raw_values = values.to_numpy(dtype=object, copy=False)
        if not all(_is_supported_tick_time(value) for value in raw_values):
            return None
    try:
        tick_times = pd.to_datetime(
            values,
            errors="coerce",
            utc=True,
            format="mixed",
        )
    except (OverflowError, TypeError, ValueError):
        return None
    if tick_times.isna().any():
        return None
    return tick_times


def _safe_object_quote(value: object) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        return np.nan
    try:
        quote = float(value)
    except (OverflowError, TypeError, ValueError):
        return np.nan
    if not np.isfinite(quote) or quote <= 0:
        return np.nan
    return quote


def _quote_prices(values: pd.Series) -> np.ndarray:
    if is_float_dtype(values.dtype) or is_integer_dtype(values.dtype):
        return values.to_numpy(dtype=np.float64, na_value=np.nan)

    raw_values = values.to_numpy(dtype=object, copy=False)
    return np.fromiter(
        (_safe_object_quote(value) for value in raw_values),
        dtype=np.float64,
        count=len(raw_values),
    )


def select_entry_tick(
    spec: ProviderTradeSpec,
    ticks: pd.DataFrame,
) -> VirtualEntry:
    """Select the first causal, direction-side tick for a virtual entry."""
    if not spec.entry_ready:
        return _blocked(spec, *spec.entry_blockers)

    if spec.direction not in {"BUY", "SELL"}:
        blocker = "missing_direction" if not spec.direction else "invalid_direction"
        return _blocked(spec, blocker)

    trigger_utc, trigger_blocker = _entry_trigger_utc(spec)
    if trigger_blocker is not None:
        return _blocked(spec, trigger_blocker)

    if not isinstance(ticks, pd.DataFrame) or ticks.empty:
        return _blocked(spec, "missing_ticks")

    missing_columns = [
        column for column in _REQUIRED_TICK_COLUMNS if column not in ticks.columns
    ]
    if missing_columns:
        return _blocked(
            spec,
            f"missing_tick_columns:{','.join(missing_columns)}",
        )

    tick_times = _normalise_tick_times(ticks["time_utc"])
    if tick_times is None:
        return _blocked(spec, "invalid_tick_times")

    try:
        time_ns = (
            tick_times.dt.as_unit("ns")
            .astype("int64")
            .to_numpy(dtype=np.int64, copy=False)
        )
    except (OverflowError, pd.errors.OutOfBoundsDatetime, ValueError):
        return _blocked(spec, "entry_threshold_out_of_range")

    side = "ask" if spec.direction == "BUY" else "bid"
    prices = _quote_prices(ticks[side])

    if len(time_ns) > 1 and np.any(time_ns[1:] < time_ns[:-1]):
        stable_order = np.argsort(time_ns, kind="stable")
        time_ns = time_ns[stable_order]
        prices = prices[stable_order]

    try:
        threshold = trigger_utc + timedelta(milliseconds=spec.latency_ms)
        threshold_ns = pd.Timestamp(threshold).value
    except (OverflowError, pd.errors.OutOfBoundsDatetime, ValueError):
        return _blocked(spec, "entry_threshold_out_of_range")
    start_index = int(np.searchsorted(time_ns, threshold_ns, side="left"))
    if start_index == len(time_ns):
        return _blocked(spec, "missing_ticks_after_entry_trigger")

    candidate_prices = prices[start_index:]
    tradable_offsets = np.flatnonzero(
        np.isfinite(candidate_prices) & (candidate_prices > 0)
    )
    if len(tradable_offsets) == 0:
        return _blocked(spec, "no_tradable_entry_tick")

    selected_index = start_index + int(tradable_offsets[0])
    selected_time = pd.Timestamp(
        int(time_ns[selected_index]),
        unit="ns",
        tz="UTC",
    ).to_pydatetime(warn=False)

    return VirtualEntry(
        status="entered",
        time_utc=selected_time.astimezone(timezone.utc),
        price=float(prices[selected_index]),
        side=side,
        latency_ms=spec.latency_ms,
        blockers=(),
    )
