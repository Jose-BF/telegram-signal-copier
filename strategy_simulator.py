"""Auditable strategy simulations over replay trades and cached MT5 ticks.

This module is intentionally conservative:

* A strategy cannot run unless the observed tick replay for that trade is exact.
* A no-op branch must leave the real MT5 result untouched.
* Strategy changes are recorded per ticket, with assumptions and blockers.

The first promoted hypothesis is ``no_be``: ignore SL moves caused by BE
management, while keeping the provider's entries, TP levels and original SL.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import observed_tick_replay_validator
import runtime_paths
import strategy_policies


DATA_DIR = runtime_paths.active_data_dir()
DEFAULT_REPLAY_FILE = DATA_DIR / "replay_trades.jsonl"
DEFAULT_BASELINE_AUDIT_FILE = DATA_DIR / "observed_tick_replay_audit.jsonl"
DEFAULT_TICK_CACHE_DIR = DATA_DIR / "ticks_cache"
DEFAULT_OUTPUT = DATA_DIR / "strategy_simulation.json"
SCHEMA_VERSION = 1
PRICE_EPSILON = 0.01
BE_PRICE_EPSILON = 0.05


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    utc = dt.astimezone(timezone.utc)
    timespec = "milliseconds" if utc.microsecond else "seconds"
    return utc.isoformat(timespec=timespec)


def _round_money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _ticket_label(ticket: dict) -> str:
    value = ticket.get("ticket") or ticket.get("position_ticket")
    if value is None:
        return "unknown"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _direction(trade: dict) -> str:
    return str(trade.get("direction") or "").upper()


def _actual_ticket_pnl(ticket: dict) -> float:
    try:
        return float(ticket.get("pnl_net") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _actual_trade_pnl(trade: dict) -> float:
    try:
        return float(trade.get("pnl_real_mt5"))
    except (TypeError, ValueError):
        return sum(_actual_ticket_pnl(ticket) for ticket in trade.get("tickets") or [])


def _directional_price_delta(direction: str, open_price: float,
                             close_price: float) -> float:
    if direction == "BUY":
        return close_price - open_price
    if direction == "SELL":
        return open_price - close_price
    return 0.0


def _infer_unit_values(trade: dict) -> list[float]:
    direction = _direction(trade)
    values: list[float] = []
    for ticket in trade.get("tickets") or []:
        try:
            open_price = float(ticket.get("open_price"))
            close_price = float(ticket.get("close_price"))
            volume = float(ticket.get("volume") or 1.0)
            pnl = float(ticket.get("pnl_net"))
        except (TypeError, ValueError):
            continue
        denominator = _directional_price_delta(
            direction, open_price, close_price) * volume
        if abs(denominator) <= PRICE_EPSILON or abs(pnl) <= PRICE_EPSILON:
            continue
        values.append(pnl / denominator)
    return values


def _unit_value_for_ticket(
    trade: dict,
    ticket: dict,
    *,
    default_unit_value: float,
    default_unit_source: str = "default_unit_value",
) -> tuple[float, str, list[str]]:
    direction = _direction(trade)
    label = _ticket_label(ticket)
    try:
        open_price = float(ticket.get("open_price"))
        close_price = float(ticket.get("close_price"))
        volume = float(ticket.get("volume") or 1.0)
        pnl = float(ticket.get("pnl_net"))
    except (TypeError, ValueError):
        return (
            float(default_unit_value),
            default_unit_source,
            [f"{default_unit_source}:{label}"],
        )
    denominator = _directional_price_delta(direction, open_price, close_price) * volume
    if abs(denominator) > PRICE_EPSILON and abs(pnl) > PRICE_EPSILON:
        return pnl / denominator, "ticket_mt5_calibrated", []

    trade_values = _infer_unit_values(trade)
    if trade_values:
        return float(np.median(trade_values)), "trade_mt5_calibrated", []

    return (
        float(default_unit_value),
        default_unit_source,
        [f"{default_unit_source}:{label}"],
    )


def _pnl_from_prices(
    trade: dict,
    ticket: dict,
    *,
    close_price: float,
    unit_value: float,
) -> float:
    direction = _direction(trade)
    open_price = float(ticket.get("open_price"))
    volume = float(ticket.get("volume") or 1.0)
    return (
        _directional_price_delta(direction, open_price, close_price)
        * volume
        * unit_value
    )


def _level_events(history: Iterable[dict], key: str) -> list[dict]:
    events: list[dict] = []
    for item in history or []:
        if item.get("status") not in (None, "confirmed", "snapshot"):
            continue
        ts = _parse_dt(item.get("ts"))
        value = item.get(key)
        if ts is None or value is None:
            continue
        try:
            level = float(value)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        events.append({
            "ts": ts,
            "level": level,
            "source": str(item.get("source") or ""),
            "raw": item,
        })
    return sorted(events, key=lambda event: event["ts"])


def _provider_level_events(
    provider_signal: dict,
    ticket_index: int,
    key: str,
    *,
    clamp_tp_to_last: bool = False,
) -> list[dict]:
    events: list[dict] = []
    for item in provider_signal.get("level_timeline") or []:
        ts = _parse_dt(
            item.get("observed_ts_utc") or item.get("telegram_ts_utc")
        )
        if ts is None:
            continue
        if key == "tp":
            targets = item.get("tps") or []
            if not targets:
                continue
            target_index = ticket_index
            if clamp_tp_to_last:
                target_index = min(target_index, len(targets) - 1)
            if target_index >= len(targets):
                continue
            value = targets[target_index]
        else:
            value = item.get("sl")
        try:
            level = float(value)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        events.append({
            "ts": ts,
            "level": level,
            "source": "canonical_provider",
            "raw": item,
        })
    return sorted(events, key=lambda event: event["ts"])


def _is_be_source(event: dict) -> bool:
    source = str(event.get("source") or "").upper()
    if "BE" in source or "BREAKEVEN" in source or "BREAK_EVEN" in source:
        return True
    return False


def _is_be_sl_event(ticket: dict, event: dict) -> bool:
    if _is_be_source(event):
        return True
    try:
        open_price = float(ticket.get("open_price"))
        level = float(event.get("level"))
    except (TypeError, ValueError):
        return False
    return abs(level - open_price) <= BE_PRICE_EPSILON


def _filter_no_be_sl_events(ticket: dict) -> tuple[list[dict], bool]:
    events = _level_events(ticket.get("sl_history") or [], "sl")
    filtered = [
        event
        for event in events
        if not _is_be_sl_event(ticket, event)
    ]
    return filtered, len(filtered) != len(events)


def _active_levels(events: list[dict], tick_ns: np.ndarray) -> np.ndarray:
    levels = np.full(len(tick_ns), np.nan, dtype=float)
    if not events or len(tick_ns) == 0:
        return levels
    event_ns = np.array(
        [pd.Timestamp(event["ts"]).value for event in events],
        dtype=np.int64,
    )
    event_levels = np.array(
        [float(event["level"]) for event in events],
        dtype=float,
    )
    indexes = np.searchsorted(event_ns, tick_ns, side="right") - 1
    valid = indexes >= 0
    levels[valid] = event_levels[indexes[valid]]
    return levels


def _normalise_ticks(ticks: pd.DataFrame) -> pd.DataFrame:
    if ticks.empty:
        return ticks.copy()
    if ticks.attrs.get("strategy_ticks_normalized"):
        return ticks
    frame = ticks.copy()
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
    if not frame["time_utc"].is_monotonic_increasing:
        frame = frame.sort_values("time_utc").reset_index(drop=True)
    frame["_time_ns"] = (
        frame["time_utc"].dt.as_unit("ns").astype("int64"))
    frame.attrs["strategy_ticks_normalized"] = True
    return frame


def _eod_horizon(opened: datetime) -> datetime:
    return datetime.combine(opened.date(), time(23, 59, 59), tzinfo=timezone.utc)


def _first_strategy_close(
    trade: dict,
    ticket: dict,
    ticks: pd.DataFrame,
    *,
    sl_events: list[dict],
    tp_events: list[dict],
    horizon_policy: str,
    forced_close_at: datetime | None = None,
) -> tuple[dict | None, list[str], list[str]]:
    blockers: list[str] = []
    assumptions: list[str] = []
    direction = _direction(trade)
    opened = _parse_dt(ticket.get("open_dt_utc"))
    if opened is None or ticket.get("open_price") is None:
        return None, [f"missing_ticket_open:{_ticket_label(ticket)}"], assumptions
    if direction not in ("BUY", "SELL"):
        return None, ["missing_direction"], assumptions
    if not sl_events:
        blockers.append(f"missing_strategy_sl:{_ticket_label(ticket)}")
    if not tp_events:
        blockers.append(f"missing_strategy_tp:{_ticket_label(ticket)}")
    if ticks.empty:
        blockers.append(f"missing_ticks:{_ticket_label(ticket)}")
    if blockers:
        return None, blockers, assumptions

    ticks = _normalise_ticks(ticks)
    horizon = _eod_horizon(opened)
    all_tick_ns = ticks["_time_ns"].to_numpy(dtype=np.int64, copy=False)
    start_idx = int(np.searchsorted(
        all_tick_ns, pd.Timestamp(opened).value, side="left"))
    end_idx = int(np.searchsorted(
        all_tick_ns, pd.Timestamp(horizon).value, side="right"))
    window = ticks.iloc[start_idx:end_idx]
    if window.empty:
        return None, [f"missing_ticks_after_open:{_ticket_label(ticket)}"], assumptions

    tick_times = window["time_utc"]
    tick_ns = window["_time_ns"].to_numpy(dtype=np.int64, copy=False)
    sl_levels = _active_levels(sl_events, tick_ns)
    tp_levels = _active_levels(tp_events, tick_ns)
    bid = window["bid"].to_numpy(dtype=float, copy=False)
    ask = window["ask"].to_numpy(dtype=float, copy=False)

    if direction == "BUY":
        side = "bid"
        side_prices = bid
        sl_touch = ~np.isnan(sl_levels) & (bid <= sl_levels)
        tp_touch = ~np.isnan(tp_levels) & (bid >= tp_levels)
    else:
        side = "ask"
        side_prices = ask
        sl_touch = ~np.isnan(sl_levels) & (ask >= sl_levels)
        tp_touch = ~np.isnan(tp_levels) & (ask <= tp_levels)

    touched = np.flatnonzero(sl_touch | tp_touch)
    touched_idx = int(touched[0]) if len(touched) > 0 else None
    forced_idx = None
    if forced_close_at is not None:
        forced_candidates = np.flatnonzero(
            tick_ns >= pd.Timestamp(forced_close_at).value)
        if len(forced_candidates) == 0:
            return None, [
                f"missing_ticks_after_management:{_ticket_label(ticket)}"
            ], assumptions
        forced_idx = int(forced_candidates[0])

    if touched_idx is not None and (
        forced_idx is None or touched_idx <= forced_idx
    ):
        idx = touched_idx
        is_sl = bool(sl_touch[idx])
        level = float(sl_levels[idx] if is_sl else tp_levels[idx])
        side_price = float(side_prices[idx])
        return {
            "reason": "sl" if is_sl else "tp",
            "close_price": round(side_price if is_sl else level, 2),
            "trigger_level": round(level, 2),
            "side": side,
            "side_price": round(side_price, 2),
            "time_utc": _iso(pd.Timestamp(tick_times.iloc[idx]).to_pydatetime()),
        }, blockers, assumptions

    if forced_idx is not None:
        close_price = float(side_prices[forced_idx])
        return {
            "reason": "management_close",
            "close_price": round(close_price, 2),
            "side": side,
            "side_price": round(close_price, 2),
            "time_utc": _iso(
                pd.Timestamp(tick_times.iloc[forced_idx]).to_pydatetime()),
        }, blockers, assumptions

    if horizon_policy != "eod_close":
        return None, [f"no_touch_before_horizon:{_ticket_label(ticket)}"], assumptions

    last = window.iloc[-1]
    close_price = float(last["bid"] if direction == "BUY" else last["ask"])
    assumptions.append("horizon_close:eod")
    return {
        "reason": "horizon_close",
        "close_price": round(close_price, 2),
        "side": "bid" if direction == "BUY" else "ask",
        "side_price": round(close_price, 2),
        "time_utc": _iso(pd.Timestamp(last["time_utc"]).to_pydatetime()),
    }, blockers, assumptions


def _unchanged_ticket_result(ticket: dict) -> dict:
    pnl = _actual_ticket_pnl(ticket)
    return {
        "ticket": ticket.get("ticket"),
        "status": "unchanged_no_strategy_event",
        "changed_rules": [],
        "actual_pnl": _round_money(pnl),
        "strategy_pnl": _round_money(pnl),
        "delta_pnl": 0.0,
        "close_reason": ticket.get("close_reason"),
        "close_time_utc": _iso(_parse_dt(ticket.get("close_dt_utc"))),
        "close_price": ticket.get("close_price"),
        "pnl_source": "mt5_actual",
        "open_time_utc": _iso(_parse_dt(ticket.get("open_dt_utc"))),
        "open_price": ticket.get("open_price"),
        "volume": float(ticket.get("volume") or 1.0),
        "blockers": [],
        "assumptions": [],
    }


def _simulate_ticket_no_be(
    trade: dict,
    ticket: dict,
    ticks: pd.DataFrame,
    *,
    default_unit_value: float,
    default_unit_source: str,
    horizon_policy: str,
) -> dict:
    sl_events, changed = _filter_no_be_sl_events(ticket)
    if not changed:
        return _unchanged_ticket_result(ticket)

    tp_events = _level_events(ticket.get("tp_history") or [], "tp")
    close, blockers, assumptions = _first_strategy_close(
        trade,
        ticket,
        ticks,
        sl_events=sl_events,
        tp_events=tp_events,
        horizon_policy=horizon_policy,
    )
    if blockers or close is None:
        return {
            "ticket": ticket.get("ticket"),
            "status": "blocked",
            "changed_rules": ["ignored_be_sl"],
            "actual_pnl": _round_money(_actual_ticket_pnl(ticket)),
            "strategy_pnl": None,
            "delta_pnl": None,
            "close_reason": None,
            "close_time_utc": None,
            "close_price": None,
            "pnl_source": None,
            "blockers": list(dict.fromkeys(blockers)),
            "assumptions": list(dict.fromkeys(assumptions)),
        }

    unit_value, pnl_source, pnl_assumptions = _unit_value_for_ticket(
        trade,
        ticket,
        default_unit_value=default_unit_value,
        default_unit_source=default_unit_source,
    )
    strategy_pnl = _pnl_from_prices(
        trade,
        ticket,
        close_price=float(close["close_price"]),
        unit_value=unit_value,
    )
    actual_pnl = _actual_ticket_pnl(ticket)
    volume = float(ticket.get("volume") or 1.0)
    assumptions.extend(pnl_assumptions)
    return {
        "ticket": ticket.get("ticket"),
        "status": "simulated",
        "changed_rules": ["ignored_be_sl"],
        "actual_pnl": _round_money(actual_pnl),
        "strategy_pnl": _round_money(strategy_pnl),
        "delta_pnl": _round_money(strategy_pnl - actual_pnl),
        "close_reason": close["reason"],
        "close_time_utc": close["time_utc"],
        "close_price": close["close_price"],
        "touch_side": close["side"],
        "touch_side_price": close["side_price"],
        "pnl_source": pnl_source,
        "open_time_utc": _iso(_parse_dt(ticket.get("open_dt_utc"))),
        "open_price": ticket.get("open_price"),
        "volume": volume,
        "unit_value": round(float(unit_value), 8),
        "pnl_per_price_unit": round(abs(volume * float(unit_value)), 8),
        "blockers": [],
        "assumptions": list(dict.fromkeys(assumptions)),
    }


def _portfolio_excursions(
    trade: dict,
    ticks: pd.DataFrame,
    ticket_results: list[dict],
    *,
    default_unit_value: float,
    default_unit_source: str,
) -> dict:
    if ticks.empty or not ticket_results:
        return {}
    ticks = _normalise_ticks(ticks)
    sources = {
        _ticket_label(ticket): ticket
        for ticket in trade.get("tickets") or []
    }
    models = []
    for result in ticket_results:
        source = sources.get(_ticket_label(result))
        opened = _parse_dt((source or {}).get("open_dt_utc"))
        closed = _parse_dt(
            result.get("close_time_utc")
            or (source or {}).get("close_dt_utc")
        )
        if (
            source is None
            or opened is None
            or closed is None
            or source.get("open_price") is None
            or result.get("strategy_pnl") is None
        ):
            return {}
        if result.get("unit_value") is not None:
            unit_value = float(result["unit_value"])
        else:
            unit_value, _source, _assumptions = _unit_value_for_ticket(
                trade,
                source,
                default_unit_value=default_unit_value,
                default_unit_source=default_unit_source,
            )
        models.append({
            "opened": opened,
            "closed": closed,
            "open_price": float(source["open_price"]),
            "volume": float(source.get("volume") or 1.0),
            "unit_value": float(unit_value),
            "realized_pnl": float(result["strategy_pnl"]),
        })

    start = min(model["opened"] for model in models)
    end = max(model["closed"] for model in models)
    all_ns = ticks["_time_ns"].to_numpy(dtype=np.int64, copy=False)
    start_idx = int(np.searchsorted(
        all_ns, pd.Timestamp(start).value, side="left"))
    end_idx = int(np.searchsorted(
        all_ns, pd.Timestamp(end).value, side="right"))
    if start_idx >= end_idx:
        return {}
    window = ticks.iloc[start_idx:end_idx]
    window_ns = window["_time_ns"].to_numpy(dtype=np.int64, copy=False)
    side_name = "bid" if _direction(trade) == "BUY" else "ask"
    side_prices = window[side_name].to_numpy(dtype=float, copy=False)
    equity = np.zeros(len(window), dtype=float)

    for model in models:
        open_idx = int(np.searchsorted(
            window_ns, pd.Timestamp(model["opened"]).value, side="left"))
        close_idx = int(np.searchsorted(
            window_ns, pd.Timestamp(model["closed"]).value, side="left"))
        close_idx = max(open_idx, min(close_idx, len(window)))
        if close_idx > open_idx:
            if _direction(trade) == "BUY":
                delta = side_prices[open_idx:close_idx] - model["open_price"]
            else:
                delta = model["open_price"] - side_prices[open_idx:close_idx]
            equity[open_idx:close_idx] += (
                delta * model["volume"] * model["unit_value"])
        if close_idx < len(window):
            equity[close_idx:] += model["realized_pnl"]

    final_pnl = sum(model["realized_pnl"] for model in models)
    max_idx = int(np.argmax(equity))
    min_idx = int(np.argmin(equity))
    mfe = max(float(equity[max_idx]), final_pnl)
    mae = min(float(equity[min_idx]), final_pnl)
    giveback = max(0.0, mfe - final_pnl)
    return {
        "mfe_pnl": _round_money(mfe),
        "mfe_time_utc": _iso(
            pd.Timestamp(window.iloc[max_idx]["time_utc"]).to_pydatetime()),
        "mae_pnl": _round_money(mae),
        "mae_time_utc": _iso(
            pd.Timestamp(window.iloc[min_idx]["time_utc"]).to_pydatetime()),
        "profit_giveback": _round_money(giveback),
        "mfe_capture_ratio": (
            round(final_pnl / mfe, 4) if mfe > 0 else None
        ),
    }


def _management_trigger(
    trade: dict,
    policy: strategy_policies.StrategyPolicy,
    *,
    provider_signal: dict | None = None,
    require_provider_timeline: bool = False,
    allow_confirmed_mt5_fallback: bool = False,
) -> tuple[datetime | None, str | None]:
    candidates: list[tuple[datetime, str]] = []
    if provider_signal is not None:
        management_rows = provider_signal.get("management_events") or []
        source = "canonical_provider_management"
    elif require_provider_timeline:
        management_rows = []
        source = "canonical_provider_management"
    else:
        management_rows = trade.get("management") or []
        source = "provider_management"
    for item in management_rows:
        action = str(
            item.get("classified_action")
            or item.get("classified")
            or item.get("action")
            or ""
        )
        available_actions = {action}
        available_actions.update(
            str(option.get("action") or "")
            for option in item.get("execution_options") or []
            if isinstance(option, dict)
        )
        if policy.trigger_action not in available_actions:
            continue
        if provider_signal is not None or require_provider_timeline:
            raw_ts = item.get("observed_ts_utc") or item.get("telegram_ts_utc")
        else:
            raw_ts = item.get("tg_ts") or item.get("ts")
        event_dt = _parse_dt(raw_ts)
        if event_dt is not None:
            candidates.append((event_dt, source))
    if candidates:
        return min(candidates, key=lambda item: item[0])
    if (
        allow_confirmed_mt5_fallback
        and policy.trigger_action == "MOVE_SL_TO_BE"
    ):
        confirmed = [
            event["ts"]
            for ticket in trade.get("tickets") or []
            for event in _level_events(ticket.get("sl_history") or [], "sl")
            if _is_be_sl_event(ticket, event)
        ]
        if confirmed:
            return min(confirmed), "confirmed_mt5_level_history"
    return None, None


def _ticket_tp_distance(
    trade: dict,
    ticket: dict,
    trigger: datetime,
    *,
    tp_events: list[dict] | None = None,
) -> float | None:
    events = (
        tp_events
        if tp_events is not None
        else [
            event
            for event in _level_events(ticket.get("tp_history") or [], "tp")
            if not _is_be_source(event)
        ]
    )
    active = [event for event in events if event["ts"] <= trigger]
    event = active[-1] if active else None
    if event is None:
        return None
    try:
        open_price = float(ticket.get("open_price"))
        target = float(event["level"])
    except (TypeError, ValueError):
        return None
    distance = _directional_price_delta(_direction(trade), open_price, target)
    return distance if distance >= 0 else None


def _ticket_actions(
    trade: dict,
    policy: strategy_policies.StrategyPolicy,
    trigger: datetime,
    *,
    provider_signal: dict | None = None,
) -> tuple[list[tuple[int, dict, str]], list[str]]:
    tickets = list(trade.get("tickets") or [])
    distances: dict[int, float] = {}
    blockers: list[str] = []
    for index, ticket in enumerate(tickets):
        tp_events = (
            _provider_level_events(provider_signal, index, "tp")
            if provider_signal is not None
            else None
        )
        distance = _ticket_tp_distance(
            trade,
            ticket,
            trigger,
            tp_events=tp_events,
        )
        if distance is None:
            blockers.append(
                f"missing_causal_tp_at_trigger:{_ticket_label(ticket)}"
            )
        else:
            distances[index] = distance
    if blockers:
        return [], blockers
    ordered = sorted(
        enumerate(tickets),
        key=lambda item: (
            distances[item[0]],
            item[0],
        ),
    )
    allocation = policy.allocation_for(len(tickets))
    actions = (
        ["close_now"] * allocation["close_now"]
        + ["move_to_be"] * allocation["move_to_be"]
        + ["runner"] * allocation["runner"]
    )
    action_by_index = {
        original_index: action
        for (original_index, _ticket), action in zip(ordered, actions)
    }
    return [
        (index, ticket, action_by_index[index])
        for index, ticket in enumerate(tickets)
    ], []


def _policy_sl_events(
    ticket: dict,
    *,
    leg_action: str,
    trigger: datetime,
    base_events: list[dict] | None = None,
) -> list[dict]:
    events = [
        event
        for event in (
            base_events
            if base_events is not None
            else _level_events(ticket.get("sl_history") or [], "sl")
        )
        if not _is_be_sl_event(ticket, event)
    ]
    if leg_action == "move_to_be":
        try:
            open_price = float(ticket.get("open_price"))
        except (TypeError, ValueError):
            return events
        events.append({
            "ts": trigger,
            "level": open_price,
            "source": "policy_be",
            "raw": {"source": "policy_be", "sl": open_price},
        })
    return sorted(events, key=lambda event: event["ts"])


def _policy_tp_events(
    ticket: dict,
    *,
    base_events: list[dict] | None = None,
) -> list[dict]:
    return [
        event
        for event in (
            base_events
            if base_events is not None
            else _level_events(ticket.get("tp_history") or [], "tp")
        )
        if not _is_be_source(event)
    ]


def _simulate_ticket_policy(
    trade: dict,
    ticket: dict,
    ticks: pd.DataFrame,
    *,
    leg_action: str,
    trigger: datetime,
    trigger_source: str,
    default_unit_value: float,
    default_unit_source: str,
    horizon_policy: str,
    money_converter=None,
    verified_utc_offset_seconds: int | None = None,
    provider_sl_events: list[dict] | None = None,
    provider_tp_events: list[dict] | None = None,
) -> dict:
    sl_events = _policy_sl_events(
        ticket,
        leg_action=leg_action,
        trigger=trigger,
        base_events=provider_sl_events,
    )
    tp_events = _policy_tp_events(
        ticket,
        base_events=provider_tp_events,
    )
    close, blockers, assumptions = _first_strategy_close(
        trade,
        ticket,
        ticks,
        sl_events=sl_events,
        tp_events=tp_events,
        horizon_policy=horizon_policy,
        forced_close_at=trigger if leg_action == "close_now" else None,
    )
    changed_rule = {
        "close_now": "closed_at_management_trigger",
        "move_to_be": "policy_be",
        "runner": "ignored_be_sl",
    }[leg_action]
    if blockers or close is None:
        return {
            "ticket": ticket.get("ticket"),
            "status": "blocked",
            "leg_action": leg_action,
            "changed_rules": [changed_rule],
            "actual_pnl": _round_money(_actual_ticket_pnl(ticket)),
            "strategy_pnl": None,
            "delta_pnl": None,
            "close_reason": None,
            "close_time_utc": None,
            "close_price": None,
            "pnl_source": None,
            "blockers": list(dict.fromkeys(blockers)),
            "assumptions": list(dict.fromkeys(assumptions)),
        }

    actual_pnl = _actual_ticket_pnl(ticket)
    volume = float(ticket.get("volume") or 1.0)
    money_status = None
    money_blockers: list[str] = []
    pnl_currency = None
    money_conversion = None
    money_formula = None
    profit_currency_pnl = None
    if money_converter is not None:
        money = money_converter.convert_leg(
            direction=_direction(trade),
            open_price=ticket.get("open_price"),
            close_price=close["close_price"],
            volume=volume,
            open_time_utc=ticket.get("open_dt_utc"),
            close_time_utc=close["time_utc"],
            verified_utc_offset_seconds=verified_utc_offset_seconds,
        )
        money_status = money.get("status")
        money_blockers = list(money.get("blockers") or [])
        pnl_currency = money.get("pnl_currency")
        money_conversion = money.get("conversion")
        money_formula = money.get("formula")
        profit_currency_pnl = money.get("profit_currency_pnl")
        if money_status != "verified" or money.get("strategy_pnl") is None:
            return {
                "ticket": ticket.get("ticket"),
                "status": "blocked",
                "leg_action": leg_action,
                "changed_rules": [changed_rule],
                "actual_pnl": _round_money(actual_pnl),
                "strategy_pnl": None,
                "delta_pnl": None,
                "close_reason": close["reason"],
                "close_time_utc": close["time_utc"],
                "close_price": close["close_price"],
                "pnl_source": "broker_money_contract_blocked",
                "pnl_currency": pnl_currency,
                "money_status": money_status or "blocked",
                "money_conversion": money_conversion,
                "money_formula": money_formula,
                "profit_currency_pnl": profit_currency_pnl,
                "money_blockers": money_blockers,
                "open_time_utc": _iso(
                    _parse_dt(ticket.get("open_dt_utc"))
                ),
                "open_price": ticket.get("open_price"),
                "volume": volume,
                "blockers": money_blockers or [
                    f"broker_money_conversion_failed:"
                    f"{_ticket_label(ticket)}"
                ],
                "assumptions": list(dict.fromkeys(assumptions)),
            }
        strategy_pnl = float(money["strategy_pnl"])
        pnl_source = "verified_broker_money_contract"
        unit_value = None
        try:
            price_delta = abs(
                _directional_price_delta(
                    _direction(trade),
                    float(ticket.get("open_price")),
                    float(close["close_price"]),
                )
            )
            pnl_per_price_unit = (
                abs(strategy_pnl) / price_delta
                if price_delta > PRICE_EPSILON
                else 0.0
            )
        except (TypeError, ValueError):
            pnl_per_price_unit = 0.0
    else:
        unit_value, pnl_source, pnl_assumptions = _unit_value_for_ticket(
            trade,
            ticket,
            default_unit_value=default_unit_value,
            default_unit_source=default_unit_source,
        )
        strategy_pnl = _pnl_from_prices(
            trade,
            ticket,
            close_price=float(close["close_price"]),
            unit_value=unit_value,
        )
        pnl_per_price_unit = abs(volume * float(unit_value))
        assumptions.extend(pnl_assumptions)
    return {
        "ticket": ticket.get("ticket"),
        "status": "simulated",
        "leg_action": leg_action,
        "changed_rules": [changed_rule],
        "actual_pnl": _round_money(actual_pnl),
        "strategy_pnl": _round_money(strategy_pnl),
        "delta_pnl": _round_money(strategy_pnl - actual_pnl),
        "close_reason": close["reason"],
        "close_time_utc": close["time_utc"],
        "close_price": close["close_price"],
        "touch_side": close["side"],
        "touch_side_price": close["side_price"],
        "pnl_source": pnl_source,
        "pnl_currency": pnl_currency,
        "money_status": money_status,
        "money_conversion": money_conversion,
        "money_formula": money_formula,
        "profit_currency_pnl": profit_currency_pnl,
        "money_blockers": money_blockers,
        "open_time_utc": _iso(_parse_dt(ticket.get("open_dt_utc"))),
        "open_price": ticket.get("open_price"),
        "volume": volume,
        "unit_value": (
            round(float(unit_value), 8)
            if unit_value is not None
            else None
        ),
        "pnl_per_price_unit": round(float(pnl_per_price_unit), 8),
        "blockers": [],
        "assumptions": list(dict.fromkeys(assumptions)),
    }


def _baseline_blockers(
    baseline_audit: dict | None,
    *,
    allow_executed_evidence: bool = False,
) -> list[str]:
    if baseline_audit is None:
        return ["missing_baseline_tick_replay"]
    status = baseline_audit.get("status")
    if status == "exact" or (
        allow_executed_evidence
        and status in {
            "external_intervention",
            "delayed_close_observation",
        }
    ):
        return []
    blockers = [f"baseline_not_exact:{status or 'unknown'}"]
    blockers.extend(baseline_audit.get("blockers") or [])
    return list(dict.fromkeys(blockers))


def simulate_trade(
    trade: dict,
    ticks: pd.DataFrame,
    *,
    strategy_name: str,
    policy: strategy_policies.StrategyPolicy | None = None,
    result_cache: dict | None = None,
    portfolio_cache: dict | None = None,
    baseline_audit: dict | None,
    default_unit_value: float = 1.0,
    default_unit_source: str = "default_unit_value",
    horizon_policy: str = "eod_close",
    provider_signal: dict | None = None,
    require_provider_timeline: bool = False,
    level_timeline_authority: str = "canonical_provider",
    money_converter=None,
    verified_utc_offset_seconds: int | None = None,
) -> dict:
    """Simulate one management strategy for one replay trade."""
    if policy is None:
        try:
            policy = strategy_policies.policy_by_id(strategy_name)
        except KeyError:
            policy = None
    if policy is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": trade.get("sig_id"),
            "status": "blocked",
            "strategy": strategy_name,
            "actual_pnl": _round_money(_actual_trade_pnl(trade)),
            "strategy_pnl": None,
            "delta_pnl": None,
            "blockers": [f"unsupported_strategy:{strategy_name}"],
            "assumptions": [],
            "tickets": [],
        }

    baseline_errors = _baseline_blockers(
        baseline_audit,
        allow_executed_evidence=level_timeline_authority == "mt5_execution",
    )
    if baseline_errors:
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": trade.get("sig_id"),
            "channel": trade.get("channel"),
            "direction": trade.get("direction"),
            "open_dt_utc": trade.get("open_dt_utc"),
            "status": "blocked",
            "strategy": strategy_name,
            "actual_pnl": _round_money(_actual_trade_pnl(trade)),
            "strategy_pnl": None,
            "delta_pnl": None,
            "blockers": baseline_errors,
            "assumptions": [],
            "tickets": [],
        }

    if (
        policy.mode != "follow_actual"
        and require_provider_timeline
        and provider_signal is None
    ):
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": trade.get("sig_id"),
            "channel": trade.get("channel"),
            "direction": trade.get("direction"),
            "open_dt_utc": trade.get("open_dt_utc"),
            "status": "blocked",
            "strategy": strategy_name,
            "policy": policy.to_dict(),
            "level_timeline_source": None,
            "management_trigger_utc": None,
            "management_trigger_source": None,
            "actual_pnl": _round_money(_actual_trade_pnl(trade)),
            "strategy_pnl": None,
            "delta_pnl": None,
            "blockers": ["missing_canonical_provider_signal"],
            "assumptions": [],
            "tickets": [],
        }

    if level_timeline_authority not in {
        "canonical_provider",
        "mt5_execution",
    }:
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": trade.get("sig_id"),
            "status": "blocked",
            "strategy": strategy_name,
            "actual_pnl": _round_money(_actual_trade_pnl(trade)),
            "strategy_pnl": None,
            "delta_pnl": None,
            "blockers": [
                f"unsupported_level_timeline_authority:"
                f"{level_timeline_authority}"
            ],
            "assumptions": [],
            "tickets": [],
        }

    tickets = trade.get("tickets") or []
    trigger, trigger_source = _management_trigger(
        trade,
        policy,
        provider_signal=provider_signal,
        require_provider_timeline=require_provider_timeline,
        allow_confirmed_mt5_fallback=(
            level_timeline_authority == "mt5_execution"
        ),
    )
    if policy.mode == "follow_actual":
        ticket_results = []
        for ticket in tickets:
            result = _unchanged_ticket_result(ticket)
            result["leg_action"] = "follow_actual"
            ticket_results.append(result)
    else:
        ticket_open_times = [
            (ticket, opened)
            for ticket in tickets
            if (opened := _parse_dt(ticket.get("open_dt_utc"))) is not None
        ]
        early_trigger_blockers: list[str] = []
        if trigger is not None and ticket_open_times:
            if trigger < min(opened for _ticket, opened in ticket_open_times):
                early_trigger_blockers = ["management_trigger_before_trade_open"]
            else:
                early_trigger_blockers = [
                    f"management_trigger_before_ticket_open:{_ticket_label(ticket)}"
                    for ticket, opened in ticket_open_times
                    if trigger < opened
                ]
        if early_trigger_blockers:
            return {
                "schema_version": SCHEMA_VERSION,
                "sig_id": trade.get("sig_id"),
                "channel": trade.get("channel"),
                "direction": trade.get("direction"),
                "open_dt_utc": trade.get("open_dt_utc"),
                "status": "blocked",
                "strategy": strategy_name,
                "policy": policy.to_dict(),
                "level_timeline_source": (
                    "canonical_provider"
                    if provider_signal is not None
                    else "execution_ticket_history"
                ),
                "management_trigger_utc": _iso(trigger),
                "management_trigger_source": trigger_source,
                "actual_pnl": _round_money(_actual_trade_pnl(trade)),
                "strategy_pnl": None,
                "delta_pnl": None,
                "blockers": early_trigger_blockers,
                "assumptions": [],
                "tickets": [],
            }
        if trigger is None:
            observed_be = any(
                _is_be_sl_event(ticket, event)
                for ticket in tickets
                for event in _level_events(ticket.get("sl_history") or [], "sl")
            )
            if observed_be:
                return {
                    "schema_version": SCHEMA_VERSION,
                    "sig_id": trade.get("sig_id"),
                    "channel": trade.get("channel"),
                    "direction": trade.get("direction"),
                    "open_dt_utc": trade.get("open_dt_utc"),
                    "status": "blocked",
                    "strategy": strategy_name,
                    "policy": policy.to_dict(),
                    "management_trigger_utc": None,
                    "management_trigger_source": None,
                    "actual_pnl": _round_money(_actual_trade_pnl(trade)),
                    "strategy_pnl": None,
                    "delta_pnl": None,
                    "blockers": [
                        f"missing_provider_management_trigger:{policy.trigger_action}"
                    ],
                    "assumptions": [],
                    "tickets": [],
                }
            ticket_results = []
            for ticket in tickets:
                result = _unchanged_ticket_result(ticket)
                result["leg_action"] = "unchanged_no_provider_trigger"
                ticket_results.append(result)
            actions = []
        else:
            actions, action_blockers = _ticket_actions(
                trade,
                policy,
                trigger,
                provider_signal=(
                    provider_signal
                    if level_timeline_authority == "canonical_provider"
                    else None
                ),
            )
            if action_blockers:
                return {
                    "schema_version": SCHEMA_VERSION,
                    "sig_id": trade.get("sig_id"),
                    "channel": trade.get("channel"),
                    "direction": trade.get("direction"),
                    "open_dt_utc": trade.get("open_dt_utc"),
                    "status": "blocked",
                    "strategy": strategy_name,
                    "policy": policy.to_dict(),
                    "management_trigger_utc": _iso(trigger),
                    "management_trigger_source": trigger_source,
                    "actual_pnl": _round_money(_actual_trade_pnl(trade)),
                    "strategy_pnl": None,
                    "delta_pnl": None,
                    "blockers": action_blockers,
                    "assumptions": [],
                    "tickets": [],
                }
            ticket_results = []
        for ticket_index, ticket, leg_action in actions:
            cache_key = (
                str(trade.get("sig_id")),
                _ticket_label(ticket),
                leg_action,
                _iso(trigger),
                trigger_source,
                horizon_policy,
                round(float(default_unit_value), 8),
                default_unit_source,
                verified_utc_offset_seconds,
            )
            cached = result_cache.get(cache_key) if result_cache is not None else None
            if cached is None:
                cached = _simulate_ticket_policy(
                    trade,
                    ticket,
                    ticks,
                    leg_action=leg_action,
                    trigger=trigger,
                    trigger_source=str(trigger_source),
                    default_unit_value=default_unit_value,
                    default_unit_source=default_unit_source,
                    horizon_policy=horizon_policy,
                    money_converter=money_converter,
                    verified_utc_offset_seconds=(
                        verified_utc_offset_seconds
                    ),
                    provider_sl_events=(
                        _provider_level_events(
                            provider_signal, ticket_index, "sl")
                        if (
                            provider_signal is not None
                            and level_timeline_authority
                            == "canonical_provider"
                        )
                        else None
                    ),
                    provider_tp_events=(
                        _provider_level_events(
                            provider_signal, ticket_index, "tp")
                        if (
                            provider_signal is not None
                            and level_timeline_authority
                            == "canonical_provider"
                        )
                        else None
                    ),
                )
                if result_cache is not None:
                    result_cache[cache_key] = cached
            ticket_results.append(dict(cached))
    blockers = list(dict.fromkeys(
        blocker
        for result in ticket_results
        for blocker in result.get("blockers") or []
    ))
    assumptions = list(dict.fromkeys(
        assumption
        for result in ticket_results
        for assumption in result.get("assumptions") or []
    ))

    actual_pnl = _actual_trade_pnl(trade)
    if blockers:
        strategy_pnl = None
        status = "blocked"
        delta = None
    else:
        strategy_pnl = sum(
            float(result.get("strategy_pnl") or 0.0)
            for result in ticket_results
        )
        if all(result["status"].startswith("unchanged") for result in ticket_results):
            status = "unchanged"
        else:
            status = "simulated"
        delta = strategy_pnl - actual_pnl

    excursions: dict = {}
    if not blockers and ticket_results:
        portfolio_key = tuple(
            (
                _ticket_label(result),
                result.get("close_time_utc"),
                result.get("close_price"),
                result.get("strategy_pnl"),
            )
            for result in ticket_results
        )
        cached_excursions = (
            portfolio_cache.get(portfolio_key)
            if portfolio_cache is not None else None
        )
        if cached_excursions is None:
            cached_excursions = _portfolio_excursions(
                trade,
                ticks,
                ticket_results,
                default_unit_value=default_unit_value,
                default_unit_source=default_unit_source,
            )
            if portfolio_cache is not None:
                portfolio_cache[portfolio_key] = cached_excursions
        excursions = dict(cached_excursions)

    return {
        "schema_version": SCHEMA_VERSION,
        "sig_id": trade.get("sig_id"),
        "channel": trade.get("channel"),
        "direction": trade.get("direction"),
        "open_dt_utc": trade.get("open_dt_utc"),
        "status": status,
        "strategy": strategy_name,
        "policy": policy.to_dict(),
        "entry_authority": "mt5_deals",
        "level_timeline_source": (
            "canonical_provider"
            if (
                provider_signal is not None
                and level_timeline_authority == "canonical_provider"
            )
            else "execution_ticket_history"
        ),
        "management_trigger_utc": _iso(trigger),
        "management_trigger_source": trigger_source,
        "actual_pnl": _round_money(actual_pnl),
        "strategy_pnl": _round_money(strategy_pnl),
        "delta_pnl": _round_money(delta),
        "blockers": blockers,
        "assumptions": assumptions,
        "tickets": ticket_results,
        **excursions,
    }


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _date_in_range(trade: dict, from_date: str | None, to_date: str | None) -> bool:
    opened = _parse_dt(trade.get("open_dt_utc") or trade.get("signal_dt_utc"))
    if opened is None:
        return False
    day = opened.date().isoformat()
    if from_date and day < from_date:
        return False
    if to_date and day > to_date:
        return False
    return True


def _baseline_by_sig(rows: Iterable[dict]) -> dict[str, dict]:
    return {
        str(row.get("sig_id")): row
        for row in rows
        if row.get("sig_id") is not None
    }


def _global_unit_value(
    trades: Iterable[dict],
    explicit_default: float | None,
) -> tuple[float, str]:
    if explicit_default is not None:
        return float(explicit_default), "cli_default_unit_value"
    values: list[float] = []
    for trade in trades:
        values.extend(_infer_unit_values(trade))
    if values:
        return float(np.median(values)), "global_mt5_calibrated"
    return 1.0, "default_unit_value"


def _summary(rows: list[dict]) -> dict:
    counts = Counter(row.get("status") for row in rows)
    actual = sum(
        float(row.get("actual_pnl") or 0.0)
        for row in rows
        if row.get("status") != "blocked"
    )
    strategy_values = [
        float(row.get("strategy_pnl") or 0.0)
        for row in rows
        if row.get("status") != "blocked"
    ]
    strategy = sum(strategy_values)
    blocker_counts = Counter(
        blocker
        for row in rows
        for blocker in row.get("blockers") or []
    )
    assumption_counts = Counter(
        assumption
        for row in rows
        for assumption in row.get("assumptions") or []
    )
    return {
        "total": len(rows),
        "simulated": counts.get("simulated", 0),
        "unchanged": counts.get("unchanged", 0),
        "blocked": counts.get("blocked", 0),
        "actual_pnl": _round_money(actual),
        "strategy_pnl": _round_money(strategy) if counts.get("blocked", 0) == 0 else None,
        "delta_pnl": _round_money(strategy - actual) if counts.get("blocked", 0) == 0 else None,
        "top_blockers": blocker_counts.most_common(20),
        "top_assumptions": assumption_counts.most_common(20),
    }


def build_simulation_report(
    trades: list[dict],
    baseline_rows: list[dict],
    *,
    strategy_name: str,
    tick_cache_dir: Path,
    from_date: str | None = None,
    to_date: str | None = None,
    default_unit_value: float | None = None,
    horizon_policy: str = "eod_close",
) -> dict:
    baselines = _baseline_by_sig(baseline_rows)
    tick_loader = observed_tick_replay_validator.ReplayTickFrameCache(tick_cache_dir)
    selected = [
        trade
        for trade in trades
        if _date_in_range(trade, from_date, to_date)
    ]
    unit_value, unit_source = _global_unit_value(selected, default_unit_value)
    rows: list[dict] = []
    for trade in selected:
        ticks, missing = tick_loader.load_ticks_for_trade(trade, pad_minutes=5)
        baseline = baselines.get(str(trade.get("sig_id")))
        if missing:
            baseline = {
                "status": "blocked",
                "blockers": list(dict.fromkeys(missing)),
            }
        rows.append(simulate_trade(
            trade,
            ticks,
            strategy_name=strategy_name,
            baseline_audit=baseline,
            default_unit_value=unit_value,
            default_unit_source=unit_source,
            horizon_policy=horizon_policy,
        ))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy": strategy_name,
        "from_date": from_date,
        "to_date": to_date,
        "horizon_policy": horizon_policy,
        "calibration": {
            "unit_value": round(unit_value, 8),
            "source": unit_source,
        },
        "summary": _summary(rows),
        "trades": rows,
    }


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run auditable management strategy simulations")
    parser.add_argument("--strategy", default="no_be")
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY_FILE)
    parser.add_argument(
        "--baseline-audit",
        type=Path,
        default=DEFAULT_BASELINE_AUDIT_FILE,
    )
    parser.add_argument("--tick-cache-dir", type=Path, default=DEFAULT_TICK_CACHE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--from-date", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--to-date", dest="to_date")
    parser.add_argument("--default-unit-value", type=float, default=None)
    parser.add_argument("--horizon-policy", default="eod_close",
                        choices=["eod_close", "block"])
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = build_simulation_report(
        load_jsonl(args.replay),
        load_jsonl(args.baseline_audit),
        strategy_name=args.strategy,
        tick_cache_dir=args.tick_cache_dir,
        from_date=args.from_date,
        to_date=args.to_date,
        default_unit_value=args.default_unit_value,
        horizon_policy=args.horizon_policy,
    )
    write_report(report, args.output)
    summary = report["summary"]
    if not args.quiet:
        print(f"Strategy simulation: {args.strategy}")
        print(f"Trades: {summary['total']}")
        print(f"Simulated: {summary['simulated']}")
        print(f"Unchanged: {summary['unchanged']}")
        print(f"Blocked: {summary['blocked']}")
        print(f"Actual P/L: {summary['actual_pnl']}")
        print(f"Strategy P/L: {summary['strategy_pnl']}")
        print(f"Delta: {summary['delta_pnl']}")
        print(f"Output: {args.output}")
    return 0 if summary["blocked"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
