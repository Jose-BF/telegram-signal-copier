"""Broker trading-session rules used by offline tick replay."""

from __future__ import annotations

from numbers import Integral

import pandas as pd


MARKET_SESSION_CONTRACT = "vantage_xauusd_standard_v1"

_OPEN_SECOND = 1 * 60 * 60 + 1 * 60
_WEEKDAY_CLOSE_SECOND = 23 * 60 * 60 + 58 * 60
_FRIDAY_CLOSE_SECOND = 23 * 60 * 60 + 57 * 60


def _verified_offset_seconds(value: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError("missing broker UTC offset evidence")
    return int(value)


def filter_tradable_ticks(
    ticks: pd.DataFrame,
    *,
    utc_offset_seconds: int | None,
) -> tuple[pd.DataFrame, int]:
    """Return only ticks inside Vantage's standard XAUUSD trade session.

    Tick timestamps remain UTC. The verified sidecar offset is used only to
    evaluate broker-server weekday and wall-clock boundaries.
    """
    offset_seconds = _verified_offset_seconds(utc_offset_seconds)
    if "time_utc" not in ticks.columns:
        raise ValueError("tick frame has no time_utc column")

    utc_times = pd.to_datetime(ticks["time_utc"], utc=True, errors="coerce")
    server_times = utc_times + pd.to_timedelta(offset_seconds, unit="s")
    weekday = server_times.dt.dayofweek
    second_of_day = (
        server_times.dt.hour * 60 * 60
        + server_times.dt.minute * 60
        + server_times.dt.second
        + server_times.dt.microsecond / 1_000_000
    )

    weekday_session = (
        weekday.between(0, 3)
        & second_of_day.ge(_OPEN_SECOND)
        & second_of_day.lt(_WEEKDAY_CLOSE_SECOND)
    )
    friday_session = (
        weekday.eq(4)
        & second_of_day.ge(_OPEN_SECOND)
        & second_of_day.lt(_FRIDAY_CLOSE_SECOND)
    )
    tradable = utc_times.notna() & (weekday_session | friday_session)

    filtered = ticks.loc[tradable].copy()
    filtered["time_utc"] = utc_times.loc[tradable]
    removed = int(len(ticks) - len(filtered))
    filtered.attrs.update(ticks.attrs)
    filtered.attrs["market_session_contract"] = MARKET_SESSION_CONTRACT
    filtered.attrs["quote_only_ticks_removed"] = removed
    return filtered, removed
