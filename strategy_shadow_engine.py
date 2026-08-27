"""Deterministic, side-effect-free tick engine for frozen strategy shadows."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import math
from typing import Iterable

from strategy_shadow_contracts import (
    ShadowAdvance,
    ShadowManagementEvent,
    ShadowPolicy,
    ShadowPosition,
    ShadowSignalState,
    ShadowTick,
    ShadowTransition,
    normalize_direction,
)


TERMINAL_STATUSES = {"closed", "cancelled", "incomplete"}


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("shadow timestamps must include a timezone")
    return parsed


def _elapsed_minutes(state: ShadowSignalState, observed_at_utc: str) -> float:
    seconds = (
        _parse_utc(observed_at_utc) - _parse_utc(state.registered_at_utc)
    ).total_seconds()
    return max(0.0, seconds / 60.0)


def _transition(
    state: ShadowSignalState,
    event: str,
    *,
    tick_msc: int | None,
    reason: str | None = None,
    **details,
) -> ShadowTransition:
    return ShadowTransition(
        event=event,
        reason=reason,
        tick_msc=tick_msc,
        state_hash=state.state_hash,
        details=details,
    )


def _with_blocker(
    state: ShadowSignalState,
    blocker: str,
) -> tuple[ShadowSignalState, bool]:
    if blocker in state.evidence_blockers:
        return state, False
    return replace(
        state,
        evidence_blockers=state.evidence_blockers + (blocker,),
        complete=False,
    ), True


def register_signal(
    policy: ShadowPolicy,
    *,
    signal_id: str,
    source_message_id: int,
    direction: str,
    registered_at_utc: str,
    registered_tick_msc: int | None,
    reference_price: float | None = None,
) -> ShadowSignalState:
    normalized = normalize_direction(direction)
    if policy.entry_mode == "adverse_reversal" and reference_price is not None:
        reference_price = float(reference_price)
        if not math.isfinite(reference_price) or reference_price <= 0.0:
            raise ValueError("reference_price must be positive and finite")
    return ShadowSignalState.new(
        signal_id=signal_id,
        source_message_id=source_message_id,
        candidate_id=policy.candidate_id,
        channel=policy.channel,
        direction=normalized,
        registered_at_utc=registered_at_utc,
        registered_tick_msc=registered_tick_msc,
        strategy_fingerprint=policy.strategy_fingerprint,
        execution_fingerprint=policy.execution_fingerprint,
        reference_price=reference_price,
    )


def _directional_move(direction: str, entry: float, exit_price: float) -> float:
    return (
        float(exit_price) - float(entry)
        if direction == "BUY"
        else float(entry) - float(exit_price)
    )


def _position_money(
    state: ShadowSignalState,
    position: ShadowPosition,
    exit_price: float,
    tick: ShadowTick,
) -> tuple[float, bool]:
    move = _directional_move(state.direction, position.entry_price, exit_price)
    factor = (
        tick.positive_eur_per_move_lot
        if move >= 0.0
        else tick.negative_eur_per_move_lot
    )
    if factor is None or not tick.money_evidence_id:
        return 0.0, False
    return round(move * position.volume * float(factor), 2), True


def _basket_money(
    state: ShadowSignalState,
    tick: ShadowTick,
) -> tuple[float, float, bool]:
    floating = 0.0
    exact = True
    exit_price = tick.executable_price(state.direction, entry=False)
    for position in state.positions:
        if position.status != "open":
            continue
        amount, amount_exact = _position_money(state, position, exit_price, tick)
        floating = round(floating + amount, 2)
        exact = exact and amount_exact
    return floating, round(state.realized_eur + floating, 2), exact


def _close_positions(
    state: ShadowSignalState,
    tick: ShadowTick,
    positions: Iterable[ShadowPosition],
    *,
    reason: str,
) -> tuple[ShadowSignalState, tuple[int, ...]]:
    indexes = {item.leg_index for item in positions if item.status == "open"}
    if not indexes:
        return state, ()
    exit_price = tick.executable_price(state.direction, entry=False)
    exact = True
    closed_indexes: list[int] = []
    updated_positions: list[ShadowPosition] = []
    for position in state.positions:
        if position.leg_index not in indexes or position.status != "open":
            updated_positions.append(position)
            continue
        realized, position_exact = _position_money(
            state, position, exit_price, tick,
        )
        exact = exact and position_exact
        updated_positions.append(replace(
            position,
            status="closed",
            close_price=exit_price,
            closed_tick_msc=tick.time_msc,
            close_reason=reason,
            realized_eur=realized,
        ))
        closed_indexes.append(position.leg_index)
    realized_total = round(
        sum(item.realized_eur for item in updated_positions), 2,
    )
    updated = replace(
        state,
        positions=tuple(updated_positions),
        realized_eur=realized_total,
    )
    if not exact:
        updated, _ = _with_blocker(updated, "money_contract_missing")
    return updated, tuple(closed_indexes)


def _entry_expired(
    policy: ShadowPolicy,
    state: ShadowSignalState,
    tick: ShadowTick,
) -> bool:
    if policy.ladder_expiry_minutes is None:
        return False
    return _elapsed_minutes(state, tick.observed_at_utc) >= float(
        policy.ladder_expiry_minutes
    )


def _new_position(
    policy: ShadowPolicy,
    state: ShadowSignalState,
    tick: ShadowTick,
    leg_index: int,
) -> tuple[ShadowPosition, bool]:
    entry_price = tick.executable_price(state.direction, entry=True)
    target_price = None
    if policy.target_steps:
        sign = 1.0 if state.direction == "BUY" else -1.0
        target_price = round(
            entry_price + sign * float(policy.target_steps[leg_index]), 8,
        )
    stop_price = None
    money_complete = True
    if policy.trailing_distance is not None:
        sign = 1.0 if state.direction == "BUY" else -1.0
        stop_price = round(
            entry_price - sign * float(policy.trailing_distance), 8,
        )
    elif policy.hard_stop_eur_per_leg is not None:
        factor = tick.negative_eur_per_move_lot
        if factor is None or not tick.money_evidence_id:
            money_complete = False
        else:
            distance = float(policy.hard_stop_eur_per_leg) / (
                float(factor) * float(policy.entry_volumes[leg_index])
            )
            sign = 1.0 if state.direction == "BUY" else -1.0
            stop_price = round(entry_price - sign * distance, 8)
    return ShadowPosition(
        leg_index=leg_index,
        volume=float(policy.entry_volumes[leg_index]),
        entry_price=entry_price,
        opened_tick_msc=tick.time_msc,
        target_price=target_price,
        stop_price=stop_price,
    ), money_complete


def _append_fill(
    policy: ShadowPolicy,
    state: ShadowSignalState,
    tick: ShadowTick,
    leg_index: int,
    transitions: list[ShadowTransition],
) -> ShadowSignalState:
    position, money_complete = _new_position(
        policy, state, tick, leg_index,
    )
    updated = replace(
        state,
        status="open",
        positions=state.positions + (position,),
    )
    if not money_complete:
        updated, blocker_added = _with_blocker(
            updated, "money_contract_missing",
        )
        if blocker_added:
            transitions.append(_transition(
                updated,
                "evidence_blocker",
                tick_msc=tick.time_msc,
                reason="money_contract_missing",
            ))
    transitions.append(_transition(
        updated,
        "virtual_fill",
        tick_msc=tick.time_msc,
        leg_index=leg_index,
        volume=position.volume,
        entry_price=position.entry_price,
        target_price=position.target_price,
        stop_price=position.stop_price,
    ))
    return updated


def _first_fill_price(state: ShadowSignalState) -> float | None:
    if not state.positions:
        return None
    return state.positions[0].entry_price


def _process_market_ladder_entries(
    policy: ShadowPolicy,
    state: ShadowSignalState,
    tick: ShadowTick,
    transitions: list[ShadowTransition],
) -> ShadowSignalState:
    expired = _entry_expired(policy, state, tick)
    if not state.positions:
        if expired:
            updated = replace(
                state, status="cancelled", exit_reason="entry_expired",
            )
            transitions.append(_transition(
                updated,
                "entry_cancelled",
                tick_msc=tick.time_msc,
                reason="entry_expired",
            ))
            return updated
        return _append_fill(policy, state, tick, 0, transitions)
    if expired:
        return state

    anchor = _first_fill_price(state)
    assert anchor is not None
    filled = {item.leg_index for item in state.positions}
    entry_price = tick.executable_price(state.direction, entry=True)
    sign = -1.0 if state.direction == "BUY" else 1.0
    updated = state
    for leg_index in range(1, len(policy.entry_volumes)):
        if leg_index in filled:
            continue
        trigger = anchor + sign * float(policy.ladder_step) * leg_index
        crossed = (
            entry_price <= trigger
            if state.direction == "BUY"
            else entry_price >= trigger
        )
        if crossed:
            updated = _append_fill(
                policy, updated, tick, leg_index, transitions,
            )
    return updated


def _process_immediate_entries(
    policy: ShadowPolicy,
    state: ShadowSignalState,
    tick: ShadowTick,
    transitions: list[ShadowTransition],
) -> ShadowSignalState:
    if state.positions:
        return state
    updated = state
    for leg_index in range(len(policy.entry_volumes)):
        updated = _append_fill(policy, updated, tick, leg_index, transitions)
    return updated


def _process_adverse_reversal_entry(
    policy: ShadowPolicy,
    state: ShadowSignalState,
    tick: ShadowTick,
    transitions: list[ShadowTransition],
) -> ShadowSignalState:
    if state.positions:
        return _process_market_ladder_entries(
            policy, state, tick, transitions,
        )
    if _entry_expired(policy, state, tick):
        updated = replace(
            state, status="cancelled", exit_reason="entry_expired",
        )
        transitions.append(_transition(
            updated,
            "entry_cancelled",
            tick_msc=tick.time_msc,
            reason="entry_expired",
        ))
        return updated

    price = tick.executable_price(state.direction, entry=True)
    reference = state.reference_price
    if reference is None:
        updated = replace(state, reference_price=price)
        transitions.append(_transition(
            updated,
            "entry_reference_set",
            tick_msc=tick.time_msc,
            reference_price=price,
        ))
        return updated

    if not state.adverse_armed:
        adverse = (
            price <= reference - float(policy.entry_adverse)
            if state.direction == "BUY"
            else price >= reference + float(policy.entry_adverse)
        )
        if not adverse:
            return state
        updated = replace(
            state,
            adverse_armed=True,
            adverse_extreme=price,
        )
        transitions.append(_transition(
            updated,
            "adverse_move_armed",
            tick_msc=tick.time_msc,
            adverse_extreme=price,
        ))
        return updated

    extreme = float(state.adverse_extreme)
    new_extreme = (
        min(extreme, price)
        if state.direction == "BUY"
        else max(extreme, price)
    )
    updated = state
    if new_extreme != extreme:
        updated = replace(state, adverse_extreme=new_extreme)
    confirmed = (
        price >= new_extreme + float(policy.entry_reversal)
        if state.direction == "BUY"
        else price <= new_extreme - float(policy.entry_reversal)
    )
    if not confirmed:
        return updated
    updated = _append_fill(policy, updated, tick, 0, transitions)
    transitions.append(_transition(
        updated,
        "entry_reversal_confirmed",
        tick_msc=tick.time_msc,
        reference_price=reference,
        adverse_extreme=new_extreme,
    ))
    return updated


def _level_hit(
    direction: str,
    price: float,
    level: float,
    *,
    target: bool,
) -> bool:
    if target:
        return price >= level if direction == "BUY" else price <= level
    return price <= level if direction == "BUY" else price >= level


def _process_price_exits(
    state: ShadowSignalState,
    tick: ShadowTick,
    transitions: list[ShadowTransition],
) -> ShadowSignalState:
    price = tick.executable_price(state.direction, entry=False)
    updated = state
    for position in tuple(updated.positions):
        if position.status != "open":
            continue
        reason = None
        if position.stop_price is not None and _level_hit(
            state.direction, price, position.stop_price, target=False,
        ):
            reason = "break_even" if position.break_even_applied else "protective_stop"
        elif position.target_price is not None and _level_hit(
            state.direction, price, position.target_price, target=True,
        ):
            reason = "target"
        if reason is None:
            continue
        updated, closed = _close_positions(
            updated, tick, (position,), reason=reason,
        )
        transitions.append(_transition(
            updated,
            "virtual_position_closed",
            tick_msc=tick.time_msc,
            reason=reason,
            leg_indexes=list(closed),
            close_price=price,
        ))
    return updated


def _process_price_protection(
    policy: ShadowPolicy,
    state: ShadowSignalState,
    tick: ShadowTick,
    transitions: list[ShadowTransition],
) -> ShadowSignalState:
    exit_price = tick.executable_price(state.direction, entry=False)
    updated_positions: list[ShadowPosition] = []
    changed: list[int] = []
    for position in state.positions:
        if position.status != "open":
            updated_positions.append(position)
            continue
        candidate = position
        if policy.break_even_trigger_xau is not None:
            favourable = _directional_move(
                state.direction, position.entry_price, exit_price,
            )
            if favourable >= float(policy.break_even_trigger_xau):
                tightens = (
                    position.stop_price is None
                    or (
                        state.direction == "BUY"
                        and position.entry_price > position.stop_price
                    )
                    or (
                        state.direction == "SELL"
                        and position.entry_price < position.stop_price
                    )
                )
                if tightens:
                    candidate = replace(
                        candidate,
                        stop_price=position.entry_price,
                        break_even_applied=True,
                    )
        if policy.trailing_distance is not None:
            sign = 1.0 if state.direction == "BUY" else -1.0
            proposed = round(
                exit_price - sign * float(policy.trailing_distance), 8,
            )
            tightens = (
                candidate.stop_price is None
                or (state.direction == "BUY" and proposed > candidate.stop_price)
                or (state.direction == "SELL" and proposed < candidate.stop_price)
            )
            if tightens:
                candidate = replace(candidate, stop_price=proposed)
        if candidate != position:
            changed.append(position.leg_index)
        updated_positions.append(candidate)
    if not changed:
        return state
    updated = replace(state, positions=tuple(updated_positions))
    transitions.append(_transition(
        updated,
        "protection_tightened",
        tick_msc=tick.time_msc,
        leg_indexes=changed,
    ))
    return updated


def _close_all_for_guard(
    state: ShadowSignalState,
    tick: ShadowTick,
    transitions: list[ShadowTransition],
    reason: str,
) -> ShadowSignalState:
    open_positions = tuple(
        item for item in state.positions if item.status == "open"
    )
    updated, closed = _close_positions(
        state, tick, open_positions, reason=reason,
    )
    updated = replace(
        updated,
        status="closed",
        exit_reason=reason,
        floating_eur=0.0,
        pending_provider_close=False,
    )
    transitions.append(_transition(
        updated,
        "basket_exit",
        tick_msc=tick.time_msc,
        reason=reason,
        leg_indexes=list(closed),
        net_eur=updated.realized_eur,
    ))
    return updated


def _process_guard(
    policy: ShadowPolicy,
    state: ShadowSignalState,
    tick: ShadowTick,
    transitions: list[ShadowTransition],
) -> ShadowSignalState:
    open_positions = [item for item in state.positions if item.status == "open"]
    if not open_positions:
        return state
    floating, total, exact = _basket_money(state, tick)
    updated = replace(
        state,
        floating_eur=floating,
        max_favourable_eur=max(state.max_favourable_eur, total),
        max_adverse_eur=min(state.max_adverse_eur, total),
        peak_total_eur=(
            total if state.peak_total_eur is None
            else max(state.peak_total_eur, total)
        ),
    )
    if not exact:
        updated, blocker_added = _with_blocker(
            updated, "money_contract_missing",
        )
        if blocker_added:
            transitions.append(_transition(
                updated,
                "evidence_blocker",
                tick_msc=tick.time_msc,
                reason="money_contract_missing",
            ))
        return updated

    if policy.basket_stop_eur is not None and total <= -float(
        policy.basket_stop_eur
    ):
        return _close_all_for_guard(
            updated, tick, transitions, "basket_stop",
        )

    if policy.profit_arm_eur is not None:
        if not updated.profit_lock_armed and total >= float(policy.profit_arm_eur):
            updated = replace(updated, profit_lock_armed=True)
            transitions.append(_transition(
                updated,
                "profit_lock_armed",
                tick_msc=tick.time_msc,
                observed_total_eur=total,
            ))
        elif updated.profit_lock_armed:
            peak = float(updated.peak_total_eur or total)
            if total <= peak - float(policy.profit_giveback_eur):
                return _close_all_for_guard(
                    updated, tick, transitions, "profit_giveback",
                )

    if policy.time_exit_minutes is not None and _elapsed_minutes(
        updated, tick.observed_at_utc,
    ) >= float(policy.time_exit_minutes):
        reason = None
        if policy.time_exit_mode == "loss_only" and total <= 0.0:
            reason = "loss_time_exit"
        elif policy.time_exit_mode == "profit_only" and total > 0.0:
            reason = "profit_time_exit"
        elif policy.time_exit_mode == "non_negative" and total >= 0.0:
            reason = "non_negative_time_exit"
        if reason is not None:
            return _close_all_for_guard(updated, tick, transitions, reason)
    return updated


def _finalize_if_exhausted(
    policy: ShadowPolicy,
    state: ShadowSignalState,
    tick: ShadowTick,
    transitions: list[ShadowTransition],
) -> ShadowSignalState:
    if state.status in TERMINAL_STATUSES:
        return state
    if not state.positions or any(
        item.status == "open" for item in state.positions
    ):
        return state
    all_legs_filled = len(state.positions) >= len(policy.entry_volumes)
    if not all_legs_filled and not _entry_expired(policy, state, tick):
        return state
    reason = state.positions[-1].close_reason or "positions_exhausted"
    updated = replace(
        state,
        status="closed",
        floating_eur=0.0,
        exit_reason=reason,
    )
    transitions.append(_transition(
        updated,
        "shadow_signal_closed",
        tick_msc=tick.time_msc,
        reason=reason,
        net_eur=updated.realized_eur,
    ))
    return updated


def advance_tick(
    policy: ShadowPolicy,
    state: ShadowSignalState,
    tick: ShadowTick,
) -> ShadowAdvance:
    if state.candidate_id != policy.candidate_id:
        raise ValueError("state and policy candidate mismatch")
    if state.strategy_fingerprint != policy.strategy_fingerprint:
        raise ValueError("strategy fingerprint mismatch")
    if state.execution_fingerprint != policy.execution_fingerprint:
        raise ValueError("execution fingerprint mismatch")
    if state.status in TERMINAL_STATUSES:
        return ShadowAdvance(state)
    if tick.identity == state.last_tick_identity:
        return ShadowAdvance(state)
    if (
        state.registered_tick_msc is not None
        and not state.positions
        and tick.time_msc <= state.registered_tick_msc
    ):
        return ShadowAdvance(state)
    if (
        state.last_tick_identity is not None
        and tick.time_msc < int(state.last_tick_identity[0])
    ):
        return ShadowAdvance(state)

    transitions: list[ShadowTransition] = []
    updated = replace(state, last_tick_identity=tick.identity)

    if updated.pending_provider_close:
        open_positions = tuple(
            item for item in updated.positions if item.status == "open"
        )
        if open_positions:
            updated = _close_all_for_guard(
                updated, tick, transitions, "provider_close",
            )
        else:
            updated = replace(
                updated,
                status="cancelled",
                pending_provider_close=False,
                exit_reason="provider_close_before_entry",
            )
            transitions.append(_transition(
                updated,
                "entry_cancelled",
                tick_msc=tick.time_msc,
                reason="provider_close_before_entry",
            ))
        return ShadowAdvance(updated, tuple(transitions))

    updated = _process_price_exits(updated, tick, transitions)

    if policy.entry_mode == "market_ladder":
        updated = _process_market_ladder_entries(
            policy, updated, tick, transitions,
        )
    elif policy.entry_mode == "immediate_multi":
        updated = _process_immediate_entries(
            policy, updated, tick, transitions,
        )
    elif policy.entry_mode == "adverse_reversal":
        updated = _process_adverse_reversal_entry(
            policy, updated, tick, transitions,
        )

    if updated.status not in TERMINAL_STATUSES:
        updated = _process_price_protection(
            policy, updated, tick, transitions,
        )
        updated = _process_guard(policy, updated, tick, transitions)
        updated = _finalize_if_exhausted(
            policy, updated, tick, transitions,
        )
    return ShadowAdvance(updated, tuple(transitions))


def _is_close_action(action: str) -> bool:
    normalized = str(action or "").upper()
    return "CLOSE" in normalized or normalized in {"EXIT", "CERRAR"}


def apply_management(
    policy: ShadowPolicy,
    state: ShadowSignalState,
    event: ShadowManagementEvent,
) -> ShadowAdvance:
    if event.signal_id != state.signal_id:
        raise ValueError("management event belongs to another signal")
    if event.event_id in state.processed_management_ids:
        return ShadowAdvance(state)
    updated = replace(
        state,
        processed_management_ids=state.processed_management_ids + (
            event.event_id,
        ),
    )
    transitions: list[ShadowTransition] = []
    action = str(event.action or "").upper()

    if policy.provider_management_mode == "ignore":
        transitions.append(_transition(
            updated,
            "provider_action_ignored",
            tick_msc=event.observed_tick_msc,
            reason=action,
        ))
        return ShadowAdvance(updated, tuple(transitions))

    if _is_close_action(action):
        if not any(item.status == "open" for item in updated.positions):
            updated = replace(
                updated,
                status="cancelled",
                exit_reason="provider_close_before_entry",
            )
            transitions.append(_transition(
                updated,
                "entry_cancelled",
                tick_msc=event.observed_tick_msc,
                reason="provider_close_before_entry",
            ))
        else:
            updated = replace(updated, pending_provider_close=True)
            transitions.append(_transition(
                updated,
                "provider_close_pending",
                tick_msc=event.observed_tick_msc,
                reason=action,
            ))
        return ShadowAdvance(updated, tuple(transitions))

    if policy.provider_management_mode == "exact":
        positions: list[ShadowPosition] = []
        changed: list[int] = []
        for position in updated.positions:
            candidate = position
            if position.status == "open" and action == "MOVE_SL_TO_BE":
                candidate = replace(
                    position,
                    stop_price=position.entry_price,
                    break_even_applied=True,
                )
            elif (
                position.status == "open"
                and action == "MOVE_SL_TO_PRICE"
                and event.price is not None
            ):
                candidate = replace(position, stop_price=float(event.price))
            if candidate != position:
                changed.append(position.leg_index)
            positions.append(candidate)
        if changed:
            updated = replace(updated, positions=tuple(positions))
            transitions.append(_transition(
                updated,
                "provider_protection_applied",
                tick_msc=event.observed_tick_msc,
                reason=action,
                leg_indexes=changed,
                price=event.price,
            ))
            return ShadowAdvance(updated, tuple(transitions))

    transitions.append(_transition(
        updated,
        "provider_action_observed",
        tick_msc=event.observed_tick_msc,
        reason=action,
    ))
    return ShadowAdvance(updated, tuple(transitions))
