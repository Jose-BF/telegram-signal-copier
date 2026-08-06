from __future__ import annotations

import re
from datetime import datetime, timezone
from math import isfinite
from typing import Iterable

import pandas as pd

from provider_zone_spec import ProviderZoneSpec
from simulation_oracle import PreparedTickWindow, prepare_tick_window


TERMINAL_ACTIONS = {
    "CLOSE_ALL",
    "CLOSE_PARTIAL",
    "PROGRESS_UPDATE",
    "SL_HIT_ANNOUNCEMENT",
    "TP_HIT_ANNOUNCEMENT",
}
TERMINAL_TEXT = re.compile(
    r"\b(?:missed|invalid(?:ated)?|cancel(?:led|ed)?|target\s*\d+)\b",
    re.IGNORECASE,
)


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


def _stable_strings(values: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _blocked(spec: ProviderZoneSpec, blockers: Iterable[object]) -> dict:
    return {
        "schema_version": 1,
        "provider_signal_id": spec.provider_signal_id,
        "status": "blocked",
        "blockers": _stable_strings(blockers),
        "touched_depths": [],
        "first_touch_by_depth": {},
        "maximum_penetration_pct": 0.0,
    }


def _cutoff(spec: ProviderZoneSpec, horizon: datetime) -> datetime:
    candidates: list[datetime] = []
    for event in spec.management_events:
        observed = _utc(event.get("observed_ts_utc"))
        if observed is None or (
            spec.ready_at_utc is not None and observed < spec.ready_at_utc
        ):
            continue
        action = str(event.get("classified_action") or "").upper()
        text = str(event.get("text") or "")
        if action in TERMINAL_ACTIONS or TERMINAL_TEXT.search(text):
            candidates.append(observed)
    return min([horizon, *candidates]) if candidates else horizon


def audit_zone_depths(
    spec: ProviderZoneSpec,
    ticks: pd.DataFrame | PreparedTickWindow,
    *,
    fractions: Iterable[float],
    horizon_at: datetime,
) -> dict:
    if spec.blockers:
        return _blocked(spec, spec.blockers)
    if spec.ready_at_utc is None or not spec.ready_states:
        return _blocked(spec, ["zone_spec_not_ready"])
    horizon = _utc(horizon_at)
    if horizon is None:
        return _blocked(spec, ["invalid_horizon_time"])
    if horizon < spec.ready_at_utc:
        return _blocked(spec, ["horizon_before_zone_activation"])
    depths = tuple(float(value) for value in fractions)
    if (
        not depths
        or any(not isfinite(value) or not 0 <= value <= 1 for value in depths)
        or len(set(depths)) != len(depths)
    ):
        return _blocked(spec, ["invalid_audit_depths"])
    prepared, tick_blockers = prepare_tick_window(ticks)
    if tick_blockers or prepared is None:
        return _blocked(spec, tick_blockers or ["invalid_tick_window"])
    if len({state.direction for state in spec.ready_states}) != 1:
        return _blocked(spec, ["direction_revision_after_ready"])

    cutoff = _cutoff(spec, horizon)
    start_ns = pd.Timestamp(spec.ready_at_utc).value
    stop_ns = pd.Timestamp(cutoff).value
    start = int(prepared.times_ns.searchsorted(start_ns, side="left"))
    stop = int(prepared.times_ns.searchsorted(stop_ns, side="right"))
    states = tuple(
        state for state in spec.ready_states if state.observed_utc <= cutoff
    )
    state_index = 0
    first_touch: dict[float, str] = {}
    maximum_depth = float("-inf")
    ticks_in_zone = 0
    for tick_index in range(start, stop):
        timestamp_ns = int(prepared.times_ns[tick_index])
        while (
            state_index + 1 < len(states)
            and pd.Timestamp(states[state_index + 1].observed_utc).value
            <= timestamp_ns
        ):
            state_index += 1
        state = states[state_index]
        quote = float(
            prepared.ask[tick_index]
            if state.direction == "BUY"
            else prepared.bid[tick_index]
        )
        lower, upper = state.zone
        width = upper - lower
        if width == 0:
            touched = quote <= upper if state.direction == "BUY" else quote >= lower
            depth = 1.0 if touched else -1.0
        elif state.direction == "BUY":
            depth = (upper - quote) / width
        else:
            depth = (quote - lower) / width
        maximum_depth = max(maximum_depth, depth)
        if lower <= quote <= upper:
            ticks_in_zone += 1
        observed = pd.Timestamp(
            timestamp_ns,
            unit="ns",
            tz="UTC",
        ).to_pydatetime().isoformat()
        for requested in depths:
            if requested not in first_touch and depth + 1e-12 >= requested:
                first_touch[requested] = observed

    touched = 0.0 in first_touch or any(value in first_touch for value in depths)
    clipped = min(1.0, max(0.0, maximum_depth)) if touched else 0.0
    touched_depths = [depth for depth in depths if depth in first_touch]
    return {
        "schema_version": 1,
        "provider_signal_id": spec.provider_signal_id,
        "status": "audited",
        "blockers": [],
        "cutoff_utc": cutoff.isoformat(),
        "touched_depths": touched_depths,
        "first_touch_by_depth": {
            str(depth): first_touch[depth] for depth in touched_depths
        },
        "maximum_penetration_pct": round(clipped * 100.0, 4),
        "ticks_in_zone": ticks_in_zone,
    }


__all__ = ["audit_zone_depths"]
