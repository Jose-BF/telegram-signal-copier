"""Read-only MT5 market inputs for the Gold trail momentum (no orders here).

Builds exactly the inputs of gold_trail_live_candidate.compute_momentum from
the live terminal: ticks of the last 5 hours before t0, the first tick of the
current broker day and 1-minute bars of the last days. MT5 history queries and
stamps use the broker server clock; ``utc_offset_seconds`` (proved from a live
tick, see broker_tick_clock) converts between server time and UTC.
"""

from __future__ import annotations

import numpy as np

import broker_tick_clock
import gold_trail_live_candidate as policy_module

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def broker_day_start_ms(t_utc_ms: int, utc_offset_seconds: int) -> int:
    """UTC ms of the server midnight that starts the broker day of ``t``."""
    off_ms = int(utc_offset_seconds) * 1000
    return (int(t_utc_ms) + off_ms) // DAY_MS * DAY_MS - off_ms


def _ticks_utc(raw, utc_offset_seconds: int) -> tuple[np.ndarray, np.ndarray]:
    if raw is None or len(raw) == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0)
    names = raw.dtype.names or ()
    if "time_msc" not in names:
        raise ValueError("MT5 ticks without time_msc")
    t = np.asarray(raw["time_msc"], dtype=np.int64) - int(utc_offset_seconds) * 1000
    bid = np.asarray(raw["bid"], dtype=float)
    ask = np.asarray(raw["ask"], dtype=float)
    ok = (bid > 0) & (ask > 0)
    order = np.argsort(t[ok], kind="stable")
    return t[ok][order], ((bid[ok] + ask[ok]) / 2.0)[order]


def momentum_now(
    mt5,
    symbol: str,
    provider_direction: str,
    t0_utc_ms: int,
    utc_offset_seconds: int,
) -> policy_module.Momentum:
    """Momentum of the provider's direction from MT5 data strictly before t0."""
    sign = policy_module.direction_sign(provider_direction)
    q = broker_tick_clock.server_query_datetime
    t0 = int(t0_utc_ms)
    # The last 20 minutes give the current price and the 15-minute move; the
    # research only needs >= 50 ticks in the last 5 hours, so the long window
    # is read only when the recent one is quiet (keeps the entry fast).
    raw = mt5.copy_ticks_range(symbol, q(t0 - 20 * 60_000, utc_offset_seconds), q(t0, utc_offset_seconds),
                               mt5.COPY_TICKS_ALL)
    tick_t, tick_mid = _ticks_utc(raw, utc_offset_seconds)
    if int((tick_t < t0).sum()) < 50:
        raw = mt5.copy_ticks_range(symbol, q(t0 - 5 * HOUR_MS, utc_offset_seconds), q(t0, utc_offset_seconds),
                                   mt5.COPY_TICKS_ALL)
        tick_t, tick_mid = _ticks_utc(raw, utc_offset_seconds)
    today = broker_day_start_ms(t0, utc_offset_seconds)
    day_open_mid = None
    first = mt5.copy_ticks_from(symbol, q(today, utc_offset_seconds), 1, mt5.COPY_TICKS_ALL)
    ft, fm = _ticks_utc(first, utc_offset_seconds)
    if len(ft) and today <= int(ft[0]) < min(today + 3 * HOUR_MS, t0):
        day_open_mid = float(fm[0])
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, q(t0 - 5 * DAY_MS, utc_offset_seconds),
                                 q(t0, utc_offset_seconds))
    if rates is None or len(rates) == 0:
        return policy_module.Momentum(status="no_bars")
    bars_t = np.asarray(rates["time"], dtype=np.int64) * 1000 - int(utc_offset_seconds) * 1000
    order = np.argsort(bars_t, kind="stable")
    bars_t = bars_t[order]
    bars_h = np.asarray(rates["high"], dtype=float)[order]
    bars_l = np.asarray(rates["low"], dtype=float)[order]
    bars_c = np.asarray(rates["close"], dtype=float)[order]
    i_today = int(np.searchsorted(bars_t, today, side="left"))
    prev = broker_day_start_ms(int(bars_t[i_today - 1]), utc_offset_seconds) if i_today > 0 else None
    return policy_module.compute_momentum(
        sign=sign, t0_ms=t0, tick_t_ms=tick_t, tick_mid=tick_mid, day_open_mid=day_open_mid,
        bars_t_ms=bars_t, bars_h=bars_h, bars_l=bars_l, bars_c=bars_c,
        broker_day_start_ms=today, prev_broker_day_start_ms=prev,
    )


def stops_level_price(mt5, symbol: str, default: float = 0.20) -> float:
    """Broker minimum stop distance in price units (trade_stops_level x point)."""
    try:
        info = mt5.symbol_info(symbol)
        level = float(getattr(info, "trade_stops_level", 0) or 0) * float(getattr(info, "point", 0) or 0)
        return max(level, 0.0) if level > 0 else float(default)
    except Exception:
        return float(default)
