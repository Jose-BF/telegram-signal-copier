from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from strategy_shadow_catalog import policy_by_id
from strategy_shadow_contracts import ShadowManagementEvent, ShadowTick
from strategy_shadow_engine import advance_tick, apply_management, register_signal


BASE = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)


def iso(minutes: float = 0.0) -> str:
    return (BASE + timedelta(minutes=minutes)).isoformat()


def tick(
    msc: int,
    *,
    bid: float,
    ask: float,
    minutes: float = 0.0,
    positive_factor: float | None = 100.0,
    negative_factor: float | None = 100.0,
) -> ShadowTick:
    return ShadowTick(
        time_msc=msc,
        bid=bid,
        ask=ask,
        observed_at_utc=iso(minutes),
        positive_eur_per_move_lot=positive_factor,
        negative_eur_per_move_lot=negative_factor,
        money_evidence_id=(
            "money-1"
            if positive_factor is not None and negative_factor is not None
            else None
        ),
    )


def new_state(
    candidate_id: str,
    *,
    direction: str = "BUY",
    reference: float | None = None,
):
    policy = policy_by_id(candidate_id)
    return policy, register_signal(
        policy,
        signal_id=f"{policy.channel}_123",
        source_message_id=123,
        direction=direction,
        registered_at_utc=iso(),
        registered_tick_msc=100,
        reference_price=reference,
    )


def test_dubai_market_and_adverse_ladder_fill_on_subsequent_ticks():
    policy, state = new_state("dubai_balanced_v1")

    first = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2))
    second = advance_tick(
        policy,
        first.state,
        tick(102, bid=4296.0, ask=4296.2),
    )

    assert [(p.leg_index, p.volume, p.entry_price) for p in first.state.positions] == [
        (0, 0.01, 4300.2),
    ]
    assert [(p.leg_index, p.volume) for p in second.state.positions] == [
        (0, 0.01),
        (1, 0.04),
    ]


def test_sell_ladder_fills_all_crossed_levels_in_rank_order():
    policy, state = new_state("dubai_frontloaded_30m_v1", direction="SELL")
    state = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2)).state

    result = advance_tick(policy, state, tick(102, bid=4312.2, ask=4312.4))

    assert [p.leg_index for p in result.state.positions] == [0, 1, 2, 3]
    assert [event.details["leg_index"] for event in result.transitions
            if event.event == "virtual_fill"] == [1, 2, 3]


def test_c490_opens_five_virtual_legs_once_on_first_tick():
    policy, state = new_state("gold_now_c490_v1", direction="SELL")

    first = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2))
    second = advance_tick(policy, first.state, tick(102, bid=4300.1, ask=4300.3))

    assert [p.volume for p in first.state.positions] == [0.01] * 5
    assert len([event for event in first.transitions if event.event == "virtual_fill"]) == 5
    assert not [event for event in second.transitions if event.event == "virtual_fill"]


def test_555_requires_adverse_move_and_reversal_before_first_fill():
    policy, state = new_state("gold_now_555_v1", reference=4300.0)

    waiting = advance_tick(policy, state, tick(101, bid=4299.0, ask=4299.2))
    armed = advance_tick(policy, waiting.state, tick(102, bid=4298.7, ask=4298.9))
    confirmed = advance_tick(policy, armed.state, tick(103, bid=4300.3, ask=4300.5))

    assert waiting.state.positions == ()
    assert armed.state.adverse_armed is True
    assert [p.volume for p in confirmed.state.positions] == [0.04]
    assert confirmed.state.positions[0].entry_price == 4300.5


def test_555_expiry_is_anchored_to_registration_time():
    policy, state = new_state("gold_now_555_v1", reference=4300.0)

    result = advance_tick(
        policy,
        state,
        tick(101, bid=4298.0, ask=4298.2, minutes=30),
    )

    assert result.state.status == "cancelled"
    assert result.state.exit_reason == "entry_expired"
    assert result.state.positions == ()


def test_555_targets_use_each_fill_and_buy_exit_quote():
    policy, state = new_state("gold_now_555_v1", reference=4300.0)
    state = advance_tick(policy, state, tick(101, bid=4298.7, ask=4298.9)).state
    state = advance_tick(policy, state, tick(102, bid=4300.2, ask=4300.4)).state
    assert state.positions[0].target_price == 4300.9

    result = advance_tick(policy, state, tick(103, bid=4300.91, ask=4301.11))

    assert result.state.positions[0].status == "closed"
    assert result.state.positions[0].close_price == 4300.91
    assert result.state.positions[0].close_reason == "target"


def test_555_trailing_stop_tightens_but_never_loosens():
    policy, state = new_state("gold_now_555_v1", reference=4300.0)
    state = advance_tick(policy, state, tick(101, bid=4298.7, ask=4298.9)).state
    state = advance_tick(policy, state, tick(102, bid=4300.2, ask=4300.4)).state
    initial_stop = state.positions[0].stop_price

    higher = advance_tick(policy, state, tick(103, bid=4300.8, ask=4301.0)).state
    lower = advance_tick(policy, higher, tick(104, bid=4300.6, ask=4300.8)).state

    assert higher.positions[0].stop_price > initial_stop
    assert lower.positions[0].stop_price == higher.positions[0].stop_price


def test_c490_hard_stop_uses_negative_money_factor_per_leg():
    policy, state = new_state("gold_now_c490_v1")

    opened = advance_tick(
        policy,
        state,
        tick(101, bid=4300.0, ask=4300.2, negative_factor=200.0),
    ).state

    assert {position.stop_price for position in opened.positions} == {4290.2}


def test_c490_missing_money_factor_marks_incomplete_and_does_not_guess_stop():
    policy, state = new_state("gold_now_c490_v1")

    result = advance_tick(
        policy,
        state,
        tick(
            101,
            bid=4300.0,
            ask=4300.2,
            positive_factor=None,
            negative_factor=None,
        ),
    )

    assert {position.stop_price for position in result.state.positions} == {None}
    assert "money_contract_missing" in result.state.evidence_blockers
    assert result.state.complete is False


def test_realized_money_uses_broker_half_up_rounding_per_leg():
    policy = replace(
        policy_by_id("gold_now_c490_v1"),
        provider_management_mode="explicit_close_only",
    )
    state = register_signal(
        policy,
        signal_id="canal2_rounding",
        source_message_id=124,
        direction="BUY",
        registered_at_utc=iso(),
        registered_tick_msc=100,
    )
    opened = advance_tick(
        policy,
        state,
        tick(101, bid=99.8, ask=100.0),
    ).state
    pending = apply_management(
        policy,
        opened,
        ShadowManagementEvent(
            event_id="close-half-cent",
            signal_id=opened.signal_id,
            action="CLOSE_ALL",
            observed_at_utc=iso(1),
        ),
    ).state

    closed = advance_tick(
        policy,
        pending,
        tick(102, bid=102.675, ask=102.875, minutes=1),
    ).state

    assert [position.realized_eur for position in closed.positions] == [2.68] * 5
    assert closed.realized_eur == 13.40


def test_c490_applies_break_even_after_favourable_twelve_xau():
    policy, state = new_state("gold_now_c490_v1")
    state = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2)).state

    result = advance_tick(policy, state, tick(102, bid=4312.3, ask=4312.5))

    assert all(position.break_even_applied for position in result.state.positions)
    assert {position.stop_price for position in result.state.positions} == {4300.2}


def test_missing_money_factor_blocks_basket_guard_without_estimating():
    policy, state = new_state("dubai_balanced_v1")
    state = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2)).state

    result = advance_tick(
        policy,
        state,
        tick(
            102,
            bid=4270.0,
            ask=4270.2,
            positive_factor=None,
            negative_factor=None,
        ),
    )

    assert result.state.status == "open"
    assert "money_contract_missing" in result.state.evidence_blockers
    assert not [event for event in result.transitions if event.event == "basket_exit"]


def test_dubai_profit_lock_uses_realized_plus_floating_eur():
    policy, state = new_state("dubai_balanced_v1")
    state = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2)).state
    armed = advance_tick(policy, state, tick(102, bid=4310.2, ask=4310.4)).state

    closed = advance_tick(policy, armed, tick(103, bid=4307.9, ask=4308.1)).state

    assert armed.profit_lock_armed is True
    assert closed.status == "closed"
    assert closed.exit_reason == "profit_giveback"


def test_dubai_basket_stop_closes_all_open_legs():
    policy, state = new_state("dubai_balanced_v1")
    state = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2)).state

    closed = advance_tick(policy, state, tick(102, bid=4275.0, ask=4275.2)).state

    assert closed.status == "closed"
    assert closed.exit_reason == "basket_stop"
    assert all(position.status == "closed" for position in closed.positions)


def test_time_exit_modes_respect_profit_sign():
    b210, b210_state = new_state("gold_now_b210_v1")
    b210_state = advance_tick(
        b210, b210_state, tick(101, bid=4300.0, ask=4300.2)
    ).state
    b210_closed = advance_tick(
        b210, b210_state, tick(102, bid=4301.0, ask=4301.2, minutes=3)
    ).state

    balanced, balanced_state = new_state("dubai_balanced_v1")
    balanced_state = advance_tick(
        balanced, balanced_state, tick(101, bid=4300.0, ask=4300.2)
    ).state
    balanced_closed = advance_tick(
        balanced,
        balanced_state,
        tick(102, bid=4299.0, ask=4299.2, minutes=40),
    ).state

    assert b210_closed.exit_reason == "profit_time_exit"
    assert balanced_closed.exit_reason == "loss_time_exit"


def test_provider_close_is_pending_until_next_unique_tick_and_deduplicated():
    policy, state = new_state("dubai_balanced_v1")
    state = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2)).state
    management = ShadowManagementEvent(
        event_id="m1",
        signal_id=state.signal_id,
        action="CLOSE_ALL",
        observed_at_utc=iso(1),
        observed_tick_msc=101,
    )

    pending = apply_management(policy, state, management)
    duplicate = apply_management(policy, pending.state, management)
    closed = advance_tick(
        policy,
        duplicate.state,
        tick(102, bid=4301.0, ask=4301.2, minutes=1.1),
    )

    assert pending.state.pending_provider_close is True
    assert duplicate.transitions == ()
    assert closed.state.exit_reason == "provider_close"


def test_provider_close_before_555_entry_waits_for_next_unique_tick():
    policy, state = new_state("gold_now_555_v1", reference=4300.0)
    event = ShadowManagementEvent(
        event_id="m1",
        signal_id=state.signal_id,
        action="EXIT",
        observed_at_utc=iso(1),
    )

    pending = apply_management(policy, state, event)
    cancelled = advance_tick(
        policy,
        pending.state,
        tick(101, bid=4300.0, ask=4300.2, minutes=1.1),
    )

    assert pending.state.pending_provider_close is True
    assert pending.state.status == "waiting"
    assert cancelled.state.status == "cancelled"
    assert cancelled.state.exit_reason == "provider_close_before_entry"


def test_555_non_negative_timer_starts_at_first_fill_not_signal_arrival():
    policy, state = new_state("gold_now_555_v1", reference=4300.0)
    state = advance_tick(
        policy,
        state,
        tick(101, bid=4298.7, ask=4298.9, minutes=19),
    ).state
    state = advance_tick(
        policy,
        state,
        tick(102, bid=4300.2, ask=4300.4, minutes=20),
    ).state

    before_due = advance_tick(
        policy,
        state,
        tick(103, bid=4300.4, ask=4300.6, minutes=180),
    ).state
    due = advance_tick(
        policy,
        before_due,
        tick(104, bid=4300.4, ask=4300.6, minutes=200),
    ).state

    assert before_due.status == "open"
    assert due.status == "closed"
    assert due.exit_reason == "non_negative_time_exit"


def test_c490_ignores_provider_management():
    policy, state = new_state("gold_now_c490_v1")
    event = ShadowManagementEvent(
        event_id="m1",
        signal_id=state.signal_id,
        action="CLOSE_ALL",
        observed_at_utc=iso(1),
    )

    result = apply_management(policy, state, event)

    assert result.state.pending_provider_close is False
    assert result.state.processed_management_ids == ("m1",)
    assert result.transitions[0].event == "provider_action_ignored"


def test_dubai_control_observes_provider_be_without_changing_its_positions():
    policy, state = new_state("dubai_balanced_v1")
    state = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2)).state
    event = ShadowManagementEvent(
        event_id="m-MOVE_SL_TO_BE",
        signal_id=state.signal_id,
        action="MOVE_SL_TO_BE",
        observed_at_utc=iso(1),
        observed_tick_msc=101,
    )

    result = apply_management(policy, state, event)

    assert result.state.positions[0].stop_price is None
    assert result.transitions[0].event == "provider_action_observed"


@pytest.mark.parametrize(
    "action, price, expected_stop",
    [
        ("MOVE_SL_TO_BE", None, 4300.2),
        ("MOVE_SL_TO_PRICE", 4298.5, 4298.5),
    ],
)
def test_explicit_provider_protection_policy_updates_open_positions(
    action,
    price,
    expected_stop,
):
    policy, _state = new_state("dubai_balanced_v1")
    policy = replace(policy, provider_protection_mode="exact")
    state = register_signal(
        policy,
        signal_id="canal1_123",
        source_message_id=123,
        direction="BUY",
        registered_at_utc=iso(),
        registered_tick_msc=100,
    )
    state = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2)).state
    event = ShadowManagementEvent(
        event_id=f"m-{action}",
        signal_id=state.signal_id,
        action=action,
        observed_at_utc=iso(1),
        observed_tick_msc=101,
        price=price,
    )

    result = apply_management(policy, state, event)

    assert result.state.positions[0].stop_price == expected_stop
    assert result.transitions[0].event == "provider_protection_applied"


def test_555_observes_but_does_not_apply_non_close_provider_management():
    policy, state = new_state("gold_now_555_v1", reference=4300.0)
    state = advance_tick(policy, state, tick(101, bid=4298.7, ask=4298.9)).state
    state = advance_tick(policy, state, tick(102, bid=4300.2, ask=4300.4)).state
    original_stop = state.positions[0].stop_price
    event = ShadowManagementEvent(
        event_id="m-be",
        signal_id=state.signal_id,
        action="MOVE_SL_TO_BE",
        observed_at_utc=iso(1),
        observed_tick_msc=102,
    )

    result = apply_management(policy, state, event)

    assert result.state.positions[0].stop_price == original_stop
    assert result.transitions[0].event == "provider_action_observed"


def test_repeated_tick_identity_does_not_advance_twice():
    policy, state = new_state("dubai_balanced_v1")
    first_tick = tick(101, bid=4300.0, ask=4300.2)
    first = advance_tick(policy, state, first_tick)

    duplicate = advance_tick(policy, first.state, first_tick)

    assert duplicate.state == first.state
    assert duplicate.transitions == ()


def test_tick_before_registration_never_fills():
    policy, state = new_state("dubai_balanced_v1")

    result = advance_tick(policy, state, tick(100, bid=4300.0, ask=4300.2))

    assert result.state.positions == ()
    assert result.transitions == ()
