from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Iterable

import numpy as np
import pandas as pd

from provider_zone_spec import ProviderZoneSpec, ZoneState
from simulation_oracle import PreparedTickWindow, prepare_tick_window
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


def simulate_zone_policy(
    spec: ProviderZoneSpec,
    ticks: pd.DataFrame | PreparedTickWindow,
    policy: ZoneEntryPolicy,
    *,
    horizon_at: datetime,
    tick_size: float = 0.01,
    money_converter=None,
) -> dict:
    del money_converter
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
        return row
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
    return row


__all__ = ["DEPTH_AUDIT_FRACTIONS", "simulate_zone_policy"]
