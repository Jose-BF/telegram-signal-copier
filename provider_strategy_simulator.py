from __future__ import annotations

from copy import deepcopy
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
from strategy_policies import StrategyPolicy
from strategy_simulator import (
    _directional_price_delta,
    _first_strategy_close,
    _management_trigger,
    _policy_sl_events,
    _provider_level_events,
    _ticket_tp_distance,
)


_REQUIRED_TICK_COLUMNS = ("time_utc", "bid", "ask")
_PREPARED_TICK_CONTRACT = "strict_bid_ask_utc_v1"


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

    side = "ask" if spec.direction == "BUY" else "bid"
    if (
        ticks.attrs.get("provider_replay_ticks_contract")
        == _PREPARED_TICK_CONTRACT
        and "_time_ns" in ticks.columns
    ):
        time_ns = ticks["_time_ns"].to_numpy(dtype=np.int64, copy=False)
        prices = ticks[side].to_numpy(dtype=np.float64, copy=False)
    else:
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


def _stable_strings(values: object) -> list[str]:
    if isinstance(values, str):
        items = (values,)
    else:
        try:
            items = tuple(values)  # type: ignore[arg-type]
        except TypeError:
            items = (values,)
    return list(dict.fromkeys(str(item) for item in items))


def _is_aware_iso_time(value: object) -> bool:
    if isinstance(value, str):
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except (OverflowError, TypeError, ValueError):
            return False
    elif isinstance(value, (pd.Timestamp, datetime)):
        if pd.isna(value):
            return False
        parsed = value
    else:
        return False
    try:
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (OverflowError, ValueError):
        return False


def _strict_tick_times(values: pd.Series) -> tuple[pd.Series | None, np.ndarray | None]:
    if isinstance(values.dtype, pd.DatetimeTZDtype):
        pass
    elif is_datetime64_any_dtype(values.dtype):
        return None, None
    else:
        raw_values = values.to_numpy(dtype=object, copy=False)
        if not all(_is_aware_iso_time(value) for value in raw_values):
            return None, None
    tick_times = _normalise_tick_times(values)
    if tick_times is None:
        return None, None
    try:
        time_ns = (
            tick_times.dt.as_unit("ns")
            .astype("int64")
            .to_numpy(dtype=np.int64, copy=False)
        )
    except (
        OverflowError,
        TypeError,
        pd.errors.OutOfBoundsDatetime,
        ValueError,
    ):
        return None, None
    return tick_times, time_ns


def _strict_quote_prices(values: pd.Series) -> np.ndarray | None:
    if is_float_dtype(values.dtype) or is_integer_dtype(values.dtype):
        try:
            prices = values.to_numpy(dtype=np.float64, na_value=np.nan)
        except (TypeError, ValueError):
            return None
    else:
        raw_values = values.to_numpy(dtype=object, copy=False)
        prices = np.fromiter(
            (_safe_object_quote(value) for value in raw_values),
            dtype=np.float64,
            count=len(raw_values),
        )
    if not np.all(np.isfinite(prices) & (prices > 0)):
        return None
    return prices


def _normalise_replay_ticks(
    ticks: object,
) -> tuple[object, str | None]:
    if not isinstance(ticks, pd.DataFrame) or ticks.empty:
        return ticks, None
    if any(column not in ticks.columns for column in _REQUIRED_TICK_COLUMNS):
        return ticks, None
    if (
        ticks.attrs.get("provider_replay_ticks_contract")
        == _PREPARED_TICK_CONTRACT
        and "_time_ns" in ticks.columns
    ):
        return ticks, None

    tick_times, time_ns = _strict_tick_times(ticks["time_utc"])
    if tick_times is None or time_ns is None:
        return ticks, "invalid_replay_tick_times"
    bid = _strict_quote_prices(ticks["bid"])
    ask = _strict_quote_prices(ticks["ask"])
    if bid is None or ask is None:
        return ticks, "invalid_replay_quotes"

    frame = ticks.copy(deep=True)
    frame["time_utc"] = tick_times.array
    frame["bid"] = bid
    frame["ask"] = ask
    if len(time_ns) > 1 and np.any(time_ns[1:] < time_ns[:-1]):
        stable_order = np.argsort(time_ns, kind="stable")
        frame = frame.iloc[stable_order].copy()
        time_ns = time_ns[stable_order]
    frame["_time_ns"] = time_ns
    frame.attrs["provider_replay_ticks_contract"] = _PREPARED_TICK_CONTRACT
    frame.attrs["strategy_ticks_normalized"] = True
    return frame, None


def prepare_replay_ticks(
    ticks: pd.DataFrame,
) -> tuple[pd.DataFrame, str | None]:
    """Strictly validate one tick frame for reuse across many policies."""
    prepared, blocker = _normalise_replay_ticks(ticks)
    if not isinstance(prepared, pd.DataFrame):
        return pd.DataFrame(), blocker or "invalid_replay_ticks"
    return prepared, blocker


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _entry_payload(entry: VirtualEntry) -> dict:
    return {
        "status": entry.status,
        "time_utc": _iso_utc(entry.time_utc),
        "price": entry.price,
        "side": entry.side,
        "latency_ms": entry.latency_ms,
        "blockers": _stable_strings(entry.blockers),
    }


def _result_row(
    spec: ProviderTradeSpec,
    policy: StrategyPolicy,
    entry: VirtualEntry,
    *,
    status: str,
    strategy_value: float | None = None,
    blockers: object = (),
    assumptions: object = (),
    legs: list[dict] | None = None,
) -> dict:
    return {
        "provider_signal_id": spec.provider_signal_id,
        "channel": spec.channel,
        "policy_id": policy.policy_id,
        "status": status,
        "result_unit": "xauusd_price_units",
        "money_status": "unverified",
        "strategy_value": strategy_value,
        "strategy_pnl": None,
        "entry": _entry_payload(entry),
        "blockers": _stable_strings(blockers),
        "assumptions": _stable_strings((
            *spec.evidence_assumptions,
            *_stable_strings(assumptions),
        )),
        "legs": list(legs or []),
    }


def _causal_provider_signal(spec: ProviderTradeSpec) -> dict:
    return {
        "provider_signal_id": spec.provider_signal_id,
        "level_timeline": tuple(
            event
            for event in spec.level_timeline
            if _is_aware_iso_time(event.get("observed_ts_utc"))
        ),
        "management_events": tuple(
            event
            for event in spec.management_events
            if _is_aware_iso_time(event.get("observed_ts_utc"))
        ),
    }


def _causal_gap_blockers(spec: ProviderTradeSpec) -> list[str]:
    prefixes = (
        "invalid_level_timeline_observed_ts:",
        "invalid_management_event_observed_ts:",
    )
    return _stable_strings(
        gap
        for gap in spec.policy_evidence_gaps
        if str(gap).startswith(prefixes)
    )


def _is_triggered_close_only(
    policy: StrategyPolicy,
    trigger: datetime | None,
) -> bool:
    return (
        trigger is not None
        and policy.mode == "risk_free_allocation"
        and policy.close_legs > 0
        and policy.be_legs == 0
        and policy.runner_legs == 0
    )


def _contextual_gap_blockers(
    spec: ProviderTradeSpec,
    policy: StrategyPolicy,
    trigger: datetime | None,
) -> list[str]:
    causal = set(_causal_gap_blockers(spec))
    close_only = _is_triggered_close_only(policy, trigger)
    blockers: list[str] = []
    for raw_gap in spec.policy_evidence_gaps:
        gap = str(raw_gap)
        if gap in causal:
            continue
        is_tp_gap = (
            gap in {"missing_provider_tp", "missing_provider_tps"}
            or gap.startswith("invalid_provider_tp:")
        )
        if close_only and is_tp_gap:
            continue
        blockers.append(gap)
    return _stable_strings(blockers)


def _virtual_trade(
    spec: ProviderTradeSpec,
    entry: VirtualEntry,
    *,
    leg_count: int,
) -> dict:
    opened = _iso_utc(entry.time_utc)
    tickets = [
        {
            "ticket": f"virtual:{spec.provider_signal_id}:{index}",
            "open_dt_utc": opened,
            "open_price": entry.price,
            "volume": spec.volume_per_leg,
        }
        for index in range(leg_count)
    ]
    return {
        "provider_signal_id": spec.provider_signal_id,
        "channel": spec.channel,
        "direction": spec.direction,
        "open_dt_utc": opened,
        "tickets": tickets,
    }


def _blocked_leg(ticket: dict, action: str, blockers: object) -> dict:
    return {
        "ticket": ticket.get("ticket"),
        "status": "blocked",
        "action": action,
        "open_time_utc": ticket.get("open_dt_utc"),
        "open_price": ticket.get("open_price"),
        "close_time_utc": None,
        "close_price": None,
        "close_reason": None,
        "touch_side": None,
        "touch_side_price": None,
        "volume": float(ticket.get("volume")),
        "strategy_value": None,
        "blockers": _stable_strings(blockers),
        "assumptions": [],
    }


def _policy_actions(
    trade: dict,
    policy: StrategyPolicy,
    trigger: datetime,
    provider_signal: dict,
    survivors: list[tuple[int, dict]],
) -> tuple[list[tuple[int, dict, str]], list[str]]:
    allocation = policy.allocation_for(len(survivors))
    active_actions = [
        action
        for action in ("close_now", "move_to_be", "runner")
        if allocation[action] > 0
    ]
    if len(active_actions) == 1:
        action = active_actions[0]
        return [
            (index, ticket, action)
            for index, ticket in survivors
        ], []

    distances: dict[int, float] = {}
    for ticket_index, ticket in survivors:
        tp_events = _provider_level_events(
            provider_signal,
            ticket_index,
            "tp",
            clamp_tp_to_last=True,
        )
        distance = _ticket_tp_distance(
            trade,
            ticket,
            trigger,
            tp_events=tp_events,
        )
        distances[ticket_index] = (
            float("inf") if distance is None else distance
        )

    ordered = sorted(
        survivors,
        key=lambda item: (distances[item[0]], item[0]),
    )
    allocated_actions = (
        ["close_now"] * allocation["close_now"]
        + ["move_to_be"] * allocation["move_to_be"]
        + ["runner"] * allocation["runner"]
    )
    action_by_index = {
        ticket_index: action
        for (ticket_index, _ticket), action in zip(
            ordered,
            allocated_actions,
            strict=True,
        )
    }
    return [
        (ticket_index, ticket, action_by_index[ticket_index])
        for ticket_index, ticket in survivors
    ], []


def _no_touch_tp_event(direction: str, opened: datetime) -> dict:
    return {
        "ts": opened,
        "level": np.inf if direction == "BUY" else 0.0,
        "source": "virtual_close_without_tp",
        "raw": {"source": "virtual_close_without_tp"},
    }


def _tp_events_with_no_touch_guard(
    trade: dict,
    ticket: dict,
    tp_events: list[dict],
) -> tuple[list[dict], list[str], list[str]]:
    if tp_events:
        return tp_events, [], []
    entry_opened = ticket.get("open_dt_utc")
    try:
        opened = datetime.fromisoformat(str(entry_opened))
    except (OverflowError, TypeError, ValueError):
        return [], [f"missing_ticket_open:{ticket.get('ticket')}"], []
    return (
        [_no_touch_tp_event(str(trade.get("direction")), opened)],
        [],
        ["provider_tp_not_required:close_now"],
    )


def _simulated_leg_result(
    trade: dict,
    ticket: dict,
    *,
    action: str,
    close: dict,
    assumptions: object = (),
) -> dict:
    strategy_value = _directional_price_delta(
        str(trade.get("direction")),
        float(ticket["open_price"]),
        float(close["close_price"]),
    )
    return {
        "ticket": ticket.get("ticket"),
        "status": "simulated",
        "action": action,
        "open_time_utc": ticket.get("open_dt_utc"),
        "open_price": float(ticket["open_price"]),
        "close_time_utc": close["time_utc"],
        "close_price": float(close["close_price"]),
        "close_reason": close["reason"],
        "touch_side": close["side"],
        "touch_side_price": float(close["side_price"]),
        "volume": float(ticket["volume"]),
        "strategy_value": float(strategy_value),
        "blockers": [],
        "assumptions": _stable_strings(assumptions),
    }


def _classify_pre_management_legs(
    trade: dict,
    ticks: pd.DataFrame,
    provider_signal: dict,
    trigger: datetime,
    policy: StrategyPolicy,
) -> tuple[dict[int, dict], list[tuple[int, dict]], list[str]]:
    trigger_ts = pd.Timestamp(trigger)
    pre_trigger_ticks = ticks.loc[ticks["time_utc"] <= trigger_ts].copy()
    close_only = _is_triggered_close_only(policy, trigger)
    results: dict[int, dict] = {}
    survivors: list[tuple[int, dict]] = []
    phase_blockers: list[str] = []

    for ticket_index, ticket in enumerate(trade.get("tickets") or []):
        sl_events = _provider_level_events(
            provider_signal,
            ticket_index,
            "sl",
        )
        tp_events = _provider_level_events(
            provider_signal,
            ticket_index,
            "tp",
            clamp_tp_to_last=True,
        )
        assumptions: list[str] = []
        if close_only:
            tp_events, guard_blockers, guard_assumptions = (
                _tp_events_with_no_touch_guard(trade, ticket, tp_events)
            )
            assumptions.extend(guard_assumptions)
            if guard_blockers:
                result = _blocked_leg(ticket, "unallocated", guard_blockers)
                result["assumptions"] = _stable_strings(assumptions)
                results[ticket_index] = result
                phase_blockers.extend(guard_blockers)
                continue

        close, blockers, close_assumptions = _first_strategy_close(
            trade,
            ticket,
            pre_trigger_ticks,
            sl_events=sl_events,
            tp_events=tp_events,
            horizon_policy="classify_survivor",
        )
        assumptions.extend(close_assumptions)
        survivor_marker = (
            f"no_touch_before_horizon:{ticket.get('ticket')}"
        )
        if close is not None and not blockers:
            results[ticket_index] = _simulated_leg_result(
                trade,
                ticket,
                action="closed_before_management",
                close=close,
                assumptions=assumptions,
            )
        elif close is None and blockers == [survivor_marker]:
            survivors.append((ticket_index, ticket))
        else:
            visible_blockers = blockers or [
                f"pre_management_classification_failed:{ticket.get('ticket')}"
            ]
            result = _blocked_leg(ticket, "unallocated", visible_blockers)
            result["assumptions"] = _stable_strings(assumptions)
            results[ticket_index] = result
            phase_blockers.extend(visible_blockers)

    return results, survivors, _stable_strings(phase_blockers)


def _simulate_virtual_leg(
    trade: dict,
    ticket: dict,
    ticket_index: int,
    ticks: pd.DataFrame,
    provider_signal: dict,
    *,
    action: str,
    trigger: datetime | None,
    policy: StrategyPolicy,
) -> dict:
    sl_events = _provider_level_events(provider_signal, ticket_index, "sl")
    tp_events = _provider_level_events(
        provider_signal,
        ticket_index,
        "tp",
        clamp_tp_to_last=True,
    )
    assumptions: list[str] = []

    if action == "follow_provider":
        policy_sl_events = sl_events
        forced_close_at = None
    else:
        if trigger is None:
            return _blocked_leg(
                ticket,
                action,
                [f"missing_provider_management_trigger:{policy.trigger_action}"],
            )
        policy_sl_events = _policy_sl_events(
            ticket,
            leg_action=action,
            trigger=trigger,
            base_events=sl_events,
        )
        forced_close_at = trigger if action == "close_now" else None

    if action == "close_now" and trigger is not None:
        if not any(event["ts"] <= trigger for event in sl_events):
            return _blocked_leg(
                ticket,
                action,
                [f"missing_causal_sl_at_trigger:{ticket.get('ticket')}"],
            )
        tp_events, guard_blockers, guard_assumptions = (
            _tp_events_with_no_touch_guard(trade, ticket, tp_events)
        )
        assumptions.extend(guard_assumptions)
        if guard_blockers:
            return _blocked_leg(ticket, action, guard_blockers)

    close, blockers, close_assumptions = _first_strategy_close(
        trade,
        ticket,
        ticks,
        sl_events=policy_sl_events,
        tp_events=tp_events,
        horizon_policy=policy.horizon_policy,
        forced_close_at=forced_close_at,
    )
    assumptions.extend(close_assumptions)
    if blockers or close is None:
        result = _blocked_leg(
            ticket,
            action,
            blockers or [f"missing_strategy_close:{ticket.get('ticket')}"],
        )
        result["assumptions"] = _stable_strings(assumptions)
        return result

    return _simulated_leg_result(
        trade,
        ticket,
        action=action,
        close=close,
        assumptions=assumptions,
    )


def simulate_provider_policy(
    spec: ProviderTradeSpec,
    ticks: pd.DataFrame,
    policy: StrategyPolicy,
    *,
    result_cache: dict | None = None,
) -> dict:
    """Replay one policy from a canonical provider signal in price units."""
    if not spec.entry_ready:
        entry = select_entry_tick(spec, ticks)
        return _result_row(
            spec,
            policy,
            entry,
            status="blocked",
            blockers=entry.blockers,
        )

    replay_ticks, replay_blocker = _normalise_replay_ticks(ticks)
    if replay_blocker is not None:
        entry = _blocked(spec, replay_blocker)
        return _result_row(
            spec,
            policy,
            entry,
            status="blocked",
            blockers=[replay_blocker],
        )

    entry = select_entry_tick(spec, replay_ticks)
    if entry.status == "blocked":
        return _result_row(
            spec,
            policy,
            entry,
            status="blocked",
            blockers=entry.blockers,
        )

    trade = _virtual_trade(
        spec,
        entry,
        leg_count=min(spec.leg_count, policy.base_leg_count),
    )
    cache_root = (
        {}
        if result_cache is None
        else result_cache.setdefault("provider_strategy_simulator_v1", {})
    )
    if not isinstance(cache_root, dict):
        raise ValueError("provider strategy result cache must contain a mapping")
    cache_identity = (
        id(spec),
        id(replay_ticks),
        _iso_utc(entry.time_utc),
        entry.price,
        entry.side,
    )
    tickets = list(trade["tickets"])
    if not tickets:
        return _result_row(
            spec,
            policy,
            entry,
            status="blocked",
            blockers=["missing_virtual_legs"],
        )

    provider_signal = _causal_provider_signal(spec)
    causal_gap_blockers = _causal_gap_blockers(spec)
    if causal_gap_blockers:
        legs = [
            _blocked_leg(ticket, "unallocated", causal_gap_blockers)
            for ticket in tickets
        ]
        return _result_row(
            spec,
            policy,
            entry,
            status="blocked",
            blockers=causal_gap_blockers,
            legs=legs,
        )

    if policy.mode == "follow_actual":
        blocker = f"unsupported_virtual_policy:{policy.policy_id}"
        legs = [
            _blocked_leg(ticket, "follow_actual", [blocker])
            for ticket in tickets
        ]
        return _result_row(
            spec,
            policy,
            entry,
            status="blocked",
            blockers=[blocker],
            legs=legs,
        )

    trigger, _trigger_source = _management_trigger(
        trade,
        policy,
        provider_signal=provider_signal,
    )
    if (
        trigger is not None
        and entry.time_utc is not None
        and trigger < entry.time_utc
    ):
        blocker = "management_trigger_before_entry"
        legs = [
            _blocked_leg(ticket, "unallocated", [blocker])
            for ticket in tickets
        ]
        return _result_row(
            spec,
            policy,
            entry,
            status="blocked",
            blockers=[blocker],
            legs=legs,
        )

    contextual_gap_blockers = _contextual_gap_blockers(
        spec,
        policy,
        trigger,
    )
    if contextual_gap_blockers:
        legs = [
            _blocked_leg(ticket, "unallocated", contextual_gap_blockers)
            for ticket in tickets
        ]
        return _result_row(
            spec,
            policy,
            entry,
            status="blocked",
            blockers=contextual_gap_blockers,
            legs=legs,
        )

    pre_management_results: dict[int, dict] = {}
    if trigger is None:
        survivors = list(enumerate(tickets))
        actions = [
            (index, ticket, "follow_provider")
            for index, ticket in survivors
        ]
        action_blockers: list[str] = []
        simulation_ticks = replay_ticks
    else:
        pre_management_key = (
            *cache_identity,
            "pre_management",
            trigger.isoformat(),
            _is_triggered_close_only(policy, trigger),
        )
        cached_pre_management = cache_root.get(pre_management_key)
        if cached_pre_management is None:
            classified = _classify_pre_management_legs(
                trade,
                replay_ticks,
                provider_signal,
                trigger,
                policy,
            )
            cache_root[pre_management_key] = deepcopy(classified)
        else:
            classified = deepcopy(cached_pre_management)
        (
            pre_management_results,
            survivors,
            phase_blockers,
        ) = classified
        if phase_blockers:
            legs = [
                pre_management_results.get(index)
                or _blocked_leg(ticket, "unallocated", phase_blockers)
                for index, ticket in enumerate(tickets)
            ]
            assumptions = _stable_strings(
                assumption
                for leg in legs
                for assumption in leg.get("assumptions") or []
            )
            return _result_row(
                spec,
                policy,
                entry,
                status="blocked",
                blockers=phase_blockers,
                assumptions=assumptions,
                legs=legs,
            )

        simulation_ticks = replay_ticks.loc[
            replay_ticks["time_utc"] >= pd.Timestamp(trigger)
        ].copy()
        if not survivors:
            actions = []
            action_blockers = []
        else:
            actions, action_blockers = _policy_actions(
                trade,
                policy,
                trigger,
                provider_signal,
                survivors,
            )
    if action_blockers:
        survivor_indexes = {index for index, _ticket in survivors}
        legs = []
        for index, ticket in enumerate(tickets):
            if index in pre_management_results:
                legs.append(pre_management_results[index])
            elif index in survivor_indexes:
                legs.append(
                    _blocked_leg(ticket, "unallocated", action_blockers)
                )
        return _result_row(
            spec,
            policy,
            entry,
            status="blocked",
            blockers=action_blockers,
            legs=legs,
        )

    simulated_results = {}
    for ticket_index, ticket, action in actions:
        leg_key = (
            *cache_identity,
            "leg",
            ticket_index,
            action,
            trigger.isoformat() if trigger is not None else None,
            policy.horizon_policy,
            policy.original_sl_policy,
            policy.tp_policy,
        )
        cached_leg = cache_root.get(leg_key)
        if cached_leg is None:
            leg = _simulate_virtual_leg(
                trade,
                ticket,
                ticket_index,
                simulation_ticks,
                provider_signal,
                action=action,
                trigger=trigger,
                policy=policy,
            )
            cache_root[leg_key] = deepcopy(leg)
        else:
            leg = deepcopy(cached_leg)
        simulated_results[ticket_index] = leg
    all_results = {**pre_management_results, **simulated_results}
    legs = [all_results[index] for index in range(len(tickets))]
    blockers = _stable_strings(
        blocker
        for leg in legs
        for blocker in leg.get("blockers") or []
    )
    assumptions = _stable_strings(
        assumption
        for leg in legs
        for assumption in leg.get("assumptions") or []
    )
    if blockers or any(leg.get("status") != "simulated" for leg in legs):
        return _result_row(
            spec,
            policy,
            entry,
            status="blocked",
            blockers=blockers,
            assumptions=assumptions,
            legs=legs,
        )

    strategy_value = sum(float(leg["strategy_value"]) for leg in legs)
    return _result_row(
        spec,
        policy,
        entry,
        status="simulated_price_path",
        strategy_value=float(strategy_value),
        blockers=(),
        assumptions=assumptions,
        legs=legs,
    )
