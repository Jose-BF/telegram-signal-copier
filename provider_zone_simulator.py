from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Iterable

import numpy as np
import pandas as pd

from provider_zone_spec import ProviderZoneSpec, ZoneState
from simulation_oracle import (
    PreparedTickWindow,
    prepare_tick_window,
    replay_first_close,
)
from zone_entry_policies import ZoneEntryPolicy


SCHEMA_VERSION = 1
DEPTH_AUDIT_FRACTIONS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
FILL_TERMINAL_ACTIONS = {
    "CLOSE_ALL",
    "CLOSE_PARTIAL",
    "PROGRESS_UPDATE",
    "SL_HIT_ANNOUNCEMENT",
    "TP_HIT_ANNOUNCEMENT",
}
FILL_CUTOFF_REASONS = {
    "CLOSE_ALL": "provider_close",
    "CLOSE_PARTIAL": "provider_close",
    "PROGRESS_UPDATE": "provider_progress",
    "SL_HIT_ANNOUNCEMENT": "provider_sl",
    "TP_HIT_ANNOUNCEMENT": "provider_tp",
}
FILL_TERMINAL_TEXT = re.compile(
    r"\b(?:missed|invalid(?:ated)?|cancel(?:led|ed)?|target\s*\d+)\b",
    re.IGNORECASE,
)


def _utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _stable_strings(values: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _base_row(
    spec: ProviderZoneSpec,
    policy: ZoneEntryPolicy,
    *,
    status: str,
    blockers: Iterable[object] = (),
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "provider_signal_id": spec.provider_signal_id,
        "channel": spec.channel,
        "policy_id": policy.policy_id,
        "status": status,
        "blockers": _stable_strings(blockers),
        "warnings": list(spec.warnings),
        "source_sha256": spec.source_sha256,
        "fill_cutoff_utc": None,
        "fill_cutoff_reason": None,
        "planned_leg_count": len(policy.depth_fractions),
        "filled_leg_count": 0,
        "planned_volume": policy.total_planned_volume,
        "filled_volume": 0.0,
        "average_fill_price": None,
        "result_unit": "xauusd_price_lots",
        "strategy_value": None,
        "money_status": "not_applicable" if status == "blocked" else "unverified",
        "strategy_pnl": None,
        "pnl_currency": None,
        "profit_currency_pnl": None,
        "money_blockers": [],
        "basket_excursions": None,
        "zone_diagnostics": {
            "touched": False,
            "first_touch_utc": None,
            "maximum_penetration_pct": 0.0,
            "touched_depths": [],
            "ticks_in_zone": 0,
            "observed_time_in_zone_ms": 0,
        },
        "filled_legs": [],
        "unfilled_legs": [],
    }


def _fill_cutoff(
    spec: ProviderZoneSpec,
    policy: ZoneEntryPolicy,
    horizon: datetime,
) -> tuple[datetime, str]:
    if policy.expiry_mode == "session_end":
        return horizon, "session_end"

    candidates: list[tuple[datetime, str]] = []
    for event in spec.management_events:
        observed = _utc(event.get("observed_ts_utc"))
        if observed is None or (
            spec.ready_at_utc is not None and observed < spec.ready_at_utc
        ):
            continue
        action = str(event.get("classified_action") or "").upper()
        text = str(event.get("text") or "")
        if action in FILL_TERMINAL_ACTIONS:
            candidates.append((observed, FILL_CUTOFF_REASONS[action]))
        elif FILL_TERMINAL_TEXT.search(text):
            candidates.append((observed, "provider_terminal_text"))
    if not candidates:
        return horizon, "session_end"
    candidates.sort(key=lambda item: item[0])
    observed, reason = candidates[0]
    return min(observed, horizon), reason


def _planned_level(state: ZoneState, depth_fraction: float) -> float:
    lower, upper = state.zone
    width = upper - lower
    if state.direction == "BUY":
        return round(upper - width * depth_fraction, 8)
    return round(lower + width * depth_fraction, 8)


def _crossing_mask(
    direction: str,
    quotes: np.ndarray,
    level: float,
) -> np.ndarray:
    if direction == "BUY":
        return quotes <= level
    return quotes >= level


def _first_crossing_index(
    direction: str,
    quotes: np.ndarray,
    level: float,
    start: int,
    stop: int,
) -> int | None:
    if start >= stop:
        return None
    positions = np.flatnonzero(
        _crossing_mask(direction, quotes[start:stop], level)
    )
    return start + int(positions[0]) if len(positions) else None


def _timestamp(prepared: PreparedTickWindow, index: int) -> datetime:
    return pd.Timestamp(
        int(prepared.times_ns[index]),
        unit="ns",
        tz="UTC",
    ).to_pydatetime()


def _segment_bounds(
    prepared: PreparedTickWindow,
    start: datetime,
    stop: datetime,
    *,
    include_stop: bool,
) -> tuple[int, int]:
    start_ns = pd.Timestamp(start).value
    stop_ns = pd.Timestamp(stop).value
    return (
        int(np.searchsorted(prepared.times_ns, start_ns, side="left")),
        int(np.searchsorted(
            prepared.times_ns,
            stop_ns,
            side="right" if include_stop else "left",
        )),
    )


def _zone_diagnostics(
    spec: ProviderZoneSpec,
    prepared: PreparedTickWindow,
    activation: datetime,
    cutoff: datetime,
) -> dict:
    maximum_depth = float("-inf")
    first_touch: datetime | None = None
    ticks_in_zone = 0
    observed_time_ms = 0
    states = tuple(
        state for state in spec.ready_states if state.observed_utc <= cutoff
    )
    for index, state in enumerate(states):
        start = max(activation, state.observed_utc)
        stop = (
            min(cutoff, states[index + 1].observed_utc)
            if index + 1 < len(states)
            else cutoff
        )
        if stop < start:
            continue
        segment_start, segment_stop = _segment_bounds(
            prepared,
            start,
            stop,
            include_stop=index + 1 == len(states),
        )
        if segment_start >= segment_stop:
            continue
        quotes = prepared.ask if state.direction == "BUY" else prepared.bid
        values = quotes[segment_start:segment_stop]
        lower, upper = state.zone
        width = upper - lower
        if width == 0:
            touched = values <= upper if state.direction == "BUY" else values >= lower
            depths = np.where(touched, 1.0, -1.0)
        elif state.direction == "BUY":
            depths = (upper - values) / width
        else:
            depths = (values - lower) / width
        segment_maximum = float(np.max(depths))
        maximum_depth = max(maximum_depth, segment_maximum)
        touch_positions = np.flatnonzero(depths >= 0)
        if len(touch_positions):
            touched_at = _timestamp(
                prepared,
                segment_start + int(touch_positions[0]),
            )
            if first_touch is None or touched_at < first_touch:
                first_touch = touched_at
        in_zone = (values >= lower) & (values <= upper)
        ticks_in_zone += int(np.count_nonzero(in_zone))
        if len(values) > 1:
            segment_times = prepared.times_ns[segment_start:segment_stop]
            elapsed_ms = np.diff(segment_times) / 1_000_000
            observed_time_ms += int(np.sum(
                np.minimum(elapsed_ms, 1000.0) * in_zone[:-1]
            ))
    touched = first_touch is not None
    clipped = min(1.0, max(0.0, maximum_depth)) if touched else 0.0
    touched_depths = [
        depth
        for depth in DEPTH_AUDIT_FRACTIONS
        if touched and clipped + 1e-12 >= depth
    ]
    return {
        "touched": touched,
        "first_touch_utc": first_touch.isoformat() if first_touch else None,
        "maximum_penetration_pct": round(clipped * 100.0, 4),
        "touched_depths": touched_depths,
        "ticks_in_zone": ticks_in_zone,
        "observed_time_in_zone_ms": observed_time_ms,
    }


def _provider_level_events(
    states: tuple[ZoneState, ...],
    leg_index: int,
) -> tuple[list[dict], list[dict]]:
    sl_events: list[dict] = []
    tp_events: list[dict] = []
    previous_sl: float | None = None
    previous_tp: float | None = None
    for state in states:
        if state.sl != previous_sl:
            sl_events.append({
                "ts": state.observed_utc,
                "level": state.sl,
                "source": "provider_zone_state",
            })
            previous_sl = state.sl
        target = state.tps[min(leg_index, len(state.tps) - 1)]
        if target != previous_tp:
            tp_events.append({
                "ts": state.observed_utc,
                "level": target,
                "source": "provider_zone_state",
            })
            previous_tp = target
    return sl_events, tp_events


def _directional_delta(
    direction: str,
    open_price: float,
    close_price: float,
) -> float:
    if direction == "BUY":
        return close_price - open_price
    return open_price - close_price


def _basket_excursions(
    direction: str,
    legs: list[dict],
    prepared: PreparedTickWindow,
    realized_value: float,
) -> dict | None:
    if not legs:
        return None
    opened = [_utc(leg.get("open_time_utc")) for leg in legs]
    closed = [_utc(leg.get("close_time_utc")) for leg in legs]
    if any(value is None for value in (*opened, *closed)):
        return None
    opened_values = [value for value in opened if value is not None]
    closed_values = [value for value in closed if value is not None]
    first_open = min(opened_values)
    last_close = max(closed_values)
    start, stop = _segment_bounds(
        prepared,
        first_open,
        last_close,
        include_stop=True,
    )
    if start >= stop:
        return None
    quote_values = prepared.bid if direction == "BUY" else prepared.ask
    floating = np.zeros(stop - start, dtype=float)
    times = prepared.times_ns[start:stop]
    for leg in legs:
        leg_open = _utc(leg["open_time_utc"])
        leg_close = _utc(leg["close_time_utc"])
        if leg_open is None or leg_close is None:
            continue
        active = (
            (times >= pd.Timestamp(leg_open).value)
            & (times <= pd.Timestamp(leg_close).value)
        )
        if not np.any(active):
            continue
        open_price = float(leg["open_price"])
        volume = float(leg["volume"])
        if direction == "BUY":
            values = (quote_values[start:stop] - open_price) * volume
        else:
            values = (open_price - quote_values[start:stop]) * volume
        floating += np.where(active, values, 0.0)
    maximum_favorable = float(np.max(floating))
    maximum_adverse = float(np.min(floating))
    return {
        "maximum_favorable_price_lots": round(maximum_favorable, 8),
        "maximum_adverse_price_lots": round(maximum_adverse, 8),
        "giveback_price_lots": round(
            max(0.0, maximum_favorable - realized_value),
            8,
        ),
        "holding_time_ms": int(round(
            (last_close - first_open).total_seconds() * 1000
        )),
        "first_open_utc": first_open.isoformat(),
        "last_close_utc": last_close.isoformat(),
    }


def _apply_exits_and_money(
    row: dict,
    spec: ProviderZoneSpec,
    policy: ZoneEntryPolicy,
    prepared: PreparedTickWindow,
    horizon: datetime,
    tick_size: float,
    money_converter,
    verified_utc_offset_seconds: int | None,
) -> dict:
    if not row["filled_legs"]:
        row["strategy_value"] = 0.0
        row["money_status"] = (
            "verified_no_fill" if money_converter is not None else "unverified"
        )
        if money_converter is not None:
            row["strategy_pnl"] = 0.0
            row["profit_currency_pnl"] = 0.0
            row["pnl_currency"] = str(money_converter.currency)
        return row

    direction = spec.ready_states[0].direction
    exit_blockers: list[str] = []
    money_blockers: list[str] = []
    money_values: list[float] = []
    profit_currency_values: list[float] = []
    priced_legs: list[dict] = []
    for leg in row["filled_legs"]:
        sl_events, tp_events = _provider_level_events(
            spec.ready_states,
            int(leg["leg_index"]),
        )
        close = replay_first_close(
            direction=direction,
            opened_at=_utc(leg["open_time_utc"]),
            open_price=leg["open_price"],
            ticks=prepared,
            sl_events=sl_events,
            tp_events=tp_events,
            horizon_at=horizon,
            tick_size=tick_size,
        )
        priced = dict(leg)
        if close.get("status") != "simulated":
            blockers = close.get("blockers") or ["unpriced_zone_leg"]
            priced["close_status"] = "blocked"
            priced["blockers"] = list(blockers)
            exit_blockers.extend(
                f"leg_{leg['leg_index']}:{blocker}" for blocker in blockers
            )
            priced_legs.append(priced)
            continue
        close_price = float(close["close_price"])
        price_delta = _directional_delta(
            direction,
            float(leg["open_price"]),
            close_price,
        )
        strategy_value = price_delta * float(leg["volume"])
        priced.update({
            "close_status": "simulated",
            "close_reason": close["close_reason"],
            "close_time_utc": close["close_time_utc"],
            "close_price": close_price,
            "trigger_level": close.get("trigger_level"),
            "exit_quote_side": close["quote_side"],
            "exit_touch_price": close["touch_price"],
            "exit_touch_bid": close["touch_bid"],
            "exit_touch_ask": close["touch_ask"],
            "exit_touch_source_index": close["touch_source_index"],
            "price_delta": round(price_delta, 8),
            "strategy_value": round(strategy_value, 8),
            "blockers": [],
        })
        if money_converter is None:
            priced["money"] = None
        else:
            money_values_for_leg = {
                "direction": direction,
                "open_price": leg["open_price"],
                "close_price": close_price,
                "volume": leg["volume"],
                "open_time_utc": leg["open_time_utc"],
                "close_time_utc": close["close_time_utc"],
            }
            if verified_utc_offset_seconds is not None:
                money_values_for_leg["verified_utc_offset_seconds"] = (
                    verified_utc_offset_seconds
                )
            money = money_converter.convert_leg(
                **money_values_for_leg,
            )
            priced["money"] = money
            if money.get("status") == "verified":
                money_values.append(float(money["strategy_pnl"]))
                if money.get("profit_currency_pnl") is not None:
                    profit_currency_values.append(
                        float(money["profit_currency_pnl"])
                    )
            else:
                money_blockers.extend(money.get("blockers") or [])
        priced_legs.append(priced)

    row["filled_legs"] = priced_legs
    if exit_blockers:
        row["status"] = "blocked"
        row["blockers"] = _stable_strings((*row["blockers"], *exit_blockers))
        row["money_status"] = "not_applicable"
        return row

    strategy_value = round(sum(
        float(leg["strategy_value"]) for leg in priced_legs
    ), 8)
    row["strategy_value"] = strategy_value
    row["basket_excursions"] = _basket_excursions(
        direction,
        priced_legs,
        prepared,
        strategy_value,
    )
    if money_converter is None:
        row["money_status"] = "unverified"
        return row
    row["pnl_currency"] = str(money_converter.currency)
    if money_blockers:
        row["money_status"] = "blocked"
        row["money_blockers"] = _stable_strings(money_blockers)
        return row
    digits = int(getattr(money_converter, "currency_digits", 2))
    row["money_status"] = "verified"
    row["strategy_pnl"] = round(sum(money_values), digits)
    row["profit_currency_pnl"] = round(
        sum(profit_currency_values),
        8,
    )
    return row


def simulate_zone_policy(
    spec: ProviderZoneSpec,
    ticks: pd.DataFrame | PreparedTickWindow,
    policy: ZoneEntryPolicy,
    *,
    horizon_at: datetime,
    tick_size: float = 0.01,
    money_converter=None,
    verified_utc_offset_seconds: int | None = None,
) -> dict:
    row = _base_row(spec, policy, status="blocked")
    if spec.blockers:
        row["blockers"] = list(spec.blockers)
        return row
    if spec.ready_at_utc is None or not spec.ready_states:
        row["blockers"] = ["zone_spec_not_ready"]
        return row
    horizon = _utc(horizon_at)
    if horizon is None:
        row["blockers"] = ["invalid_horizon_time"]
        return row
    activation = spec.ready_at_utc + timedelta(
        milliseconds=policy.activation_latency_ms
    )
    if horizon < activation:
        row["blockers"] = ["horizon_before_zone_activation"]
        return row
    if not isfinite(float(tick_size)) or tick_size <= 0:
        row["blockers"] = ["invalid_tick_size"]
        return row
    if len({state.direction for state in spec.ready_states}) != 1:
        row["blockers"] = ["direction_revision_after_ready"]
        return row
    if any(
        state.zone[0] == state.zone[1]
        for state in spec.ready_states
    ) and len(set(policy.depth_fractions)) > 1:
        row["blockers"] = ["zero_width_zone_for_layered_policy"]
        return row

    prepared, tick_blockers = prepare_tick_window(ticks)
    if tick_blockers or prepared is None:
        row["blockers"] = tick_blockers or ["invalid_tick_window"]
        return row
    cutoff, cutoff_reason = _fill_cutoff(spec, policy, horizon)
    row["fill_cutoff_utc"] = cutoff.isoformat()
    row["fill_cutoff_reason"] = cutoff_reason
    row["zone_diagnostics"] = _zone_diagnostics(
        spec,
        prepared,
        activation,
        cutoff,
    )

    states = tuple(
        state for state in spec.ready_states if state.observed_utc <= cutoff
    )
    if not states:
        row["status"] = "unfilled"
        return _apply_exits_and_money(
            row,
            spec,
            policy,
            prepared,
            horizon,
            tick_size,
            money_converter,
            verified_utc_offset_seconds,
        )
    filled: dict[int, dict] = {}
    planned_levels: dict[int, float] = {}
    market_triggered = False
    quote_values = (
        prepared.ask if states[0].direction == "BUY" else prepared.bid
    )
    market_indices = [
        index
        for index, mode in enumerate(policy.order_modes)
        if mode == "market"
    ]

    for state_index, active_state in enumerate(states):
        start = max(activation, active_state.observed_utc)
        next_state_at = (
            states[state_index + 1].observed_utc
            if state_index + 1 < len(states)
            else cutoff
        )
        stop = min(cutoff, next_state_at)
        include_stop = state_index + 1 == len(states)
        segment_start, segment_stop = _segment_bounds(
            prepared,
            start,
            stop,
            include_stop=include_stop,
        )
        for leg_index, depth in enumerate(policy.depth_fractions):
            if leg_index not in filled:
                planned_levels[leg_index] = _planned_level(active_state, depth)
        if segment_start >= segment_stop:
            continue

        if market_indices and not market_triggered:
            trigger_leg = market_indices[0]
            trigger_level = planned_levels[trigger_leg]
            trigger_index = _first_crossing_index(
                active_state.direction,
                quote_values,
                trigger_level,
                segment_start,
                segment_stop,
            )
            if trigger_index is not None:
                market_triggered = True
                trigger_time = _timestamp(prepared, trigger_index)
                for market_order, leg_index in enumerate(market_indices):
                    scheduled = trigger_time + timedelta(
                        milliseconds=(
                            market_order * policy.market_leg_spacing_ms
                        )
                    )
                    if scheduled > cutoff:
                        continue
                    scheduled_ns = pd.Timestamp(scheduled).value
                    fill_index = int(np.searchsorted(
                        prepared.times_ns,
                        scheduled_ns,
                        side="left",
                    ))
                    if (
                        fill_index >= len(prepared.times_ns)
                        or _timestamp(prepared, fill_index) > cutoff
                    ):
                        continue
                    quote = float(quote_values[fill_index])
                    filled[leg_index] = {
                        "leg_index": leg_index,
                        "depth_fraction": float(
                            policy.depth_fractions[leg_index]
                        ),
                        "order_mode": "market",
                        "volume": float(policy.volumes[leg_index]),
                        "planned_level": planned_levels[leg_index],
                        "open_time_utc": _timestamp(
                            prepared, fill_index
                        ).isoformat(),
                        "open_price": round(quote, 8),
                        "touch_side": (
                            "ask" if active_state.direction == "BUY" else "bid"
                        ),
                        "touch_price": round(quote, 8),
                        "touch_bid": round(float(prepared.bid[fill_index]), 8),
                        "touch_ask": round(float(prepared.ask[fill_index]), 8),
                        "touch_source_index": int(
                            prepared.source_indices[fill_index]
                        ),
                        "state_observed_utc": active_state.observed_utc.isoformat(),
                    }

        for leg_index, mode in enumerate(policy.order_modes):
            if mode != "limit" or leg_index in filled:
                continue
            level = planned_levels[leg_index]
            fill_index = _first_crossing_index(
                active_state.direction,
                quote_values,
                level,
                segment_start,
                segment_stop,
            )
            if fill_index is None:
                continue
            quote = float(quote_values[fill_index])
            filled[leg_index] = {
                "leg_index": leg_index,
                "depth_fraction": float(policy.depth_fractions[leg_index]),
                "order_mode": "limit",
                "volume": float(policy.volumes[leg_index]),
                "planned_level": level,
                "open_time_utc": _timestamp(prepared, fill_index).isoformat(),
                "open_price": level,
                "touch_side": (
                    "ask" if active_state.direction == "BUY" else "bid"
                ),
                "touch_price": round(quote, 8),
                "touch_bid": round(float(prepared.bid[fill_index]), 8),
                "touch_ask": round(float(prepared.ask[fill_index]), 8),
                "touch_source_index": int(
                    prepared.source_indices[fill_index]
                ),
                "state_observed_utc": active_state.observed_utc.isoformat(),
            }

    filled_legs = sorted(
        filled.values(),
        key=lambda leg: (leg["open_time_utc"], leg["leg_index"]),
    )
    unfilled_legs = [
        {
            "leg_index": leg_index,
            "depth_fraction": float(depth),
            "order_mode": policy.order_modes[leg_index],
            "volume": float(policy.volumes[leg_index]),
            "planned_level": planned_levels.get(
                leg_index,
                _planned_level(states[-1], depth),
            ),
            "cancel_reason": cutoff_reason,
            "cancel_time_utc": cutoff.isoformat(),
        }
        for leg_index, depth in enumerate(policy.depth_fractions)
        if leg_index not in filled
    ]
    filled_volume = sum(float(leg["volume"]) for leg in filled_legs)
    weighted_entry = (
        sum(
            float(leg["open_price"]) * float(leg["volume"])
            for leg in filled_legs
        ) / filled_volume
        if filled_volume > 0
        else None
    )
    row.update({
        "status": "filled" if filled_legs else "unfilled",
        "filled_leg_count": len(filled_legs),
        "filled_volume": round(filled_volume, 8),
        "average_fill_price": (
            round(weighted_entry, 8) if weighted_entry is not None else None
        ),
        "filled_legs": filled_legs,
        "unfilled_legs": unfilled_legs,
    })
    return _apply_exits_and_money(
        row,
        spec,
        policy,
        prepared,
        horizon,
        tick_size,
        money_converter,
        verified_utc_offset_seconds,
    )


__all__ = ["DEPTH_AUDIT_FRACTIONS", "simulate_zone_policy"]
