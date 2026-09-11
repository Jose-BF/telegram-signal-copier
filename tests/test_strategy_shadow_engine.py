from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from strategy_shadow_catalog import policy_by_id
from strategy_shadow_contracts import (
    ShadowManagementEvent,
    ShadowSignalState,
    ShadowTick,
)
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


@pytest.mark.parametrize("pending,flat,expected_status", [
    ("until_expiry", "keep_if_eligible", "open"),
    ("until_expiry", "finalize", "closed"),
    ("none", "keep_if_eligible", "closed"),
])
def test_terminal_rules_control_flat_basket_reentry(pending, flat, expected_status):
    policy = replace(policy_by_id("dubai_balanced_v1"),
                     pending_entry_policy=pending, automatic_flat_policy=flat,
                     target_steps=(0.5, 0.5, 0.5), basket_stop_eur=None)
    state = register_signal(policy, signal_id="canal1_123", source_message_id=123,
                            direction="BUY", registered_at_utc=iso(), registered_tick_msc=100)
    state = advance_tick(policy, state, tick(101, bid=4300, ask=4300.2)).state
    flat_state = advance_tick(policy, state, tick(102, bid=4301, ask=4301.2)).state
    later = advance_tick(policy, flat_state, tick(103, bid=4296, ask=4296.2)).state

    assert flat_state.status == expected_status
    assert len(later.positions) == (2 if expected_status == "open" else 1)


def test_finalize_happens_before_same_tick_pending_fill_after_stop():
    policy = replace(policy_by_id("dubai_balanced_v1"),
                     automatic_flat_policy="finalize", trailing_distance=1,
                     basket_stop_eur=None)
    state = register_signal(policy, signal_id="canal1_123", source_message_id=123,
                            direction="BUY", registered_at_utc=iso(), registered_tick_msc=100)
    state = advance_tick(policy, state, tick(101, bid=4300, ask=4300.2)).state
    result = advance_tick(policy, state, tick(102, bid=4295, ask=4295.2))

    assert result.state.status == "closed"
    assert len(result.state.positions) == 1
    assert not any(t.event == "virtual_fill" for t in result.transitions)


def test_no_pending_policy_does_not_add_legs_while_initial_leg_open():
    policy = replace(policy_by_id("dubai_balanced_v1"), pending_entry_policy="none",
                     basket_stop_eur=None)
    state = register_signal(policy, signal_id="canal1_123", source_message_id=123,
                            direction="BUY", registered_at_utc=iso(), registered_tick_msc=100)
    state = advance_tick(policy, state, tick(101, bid=4300, ask=4300.2)).state
    state = advance_tick(policy, state, tick(102, bid=4295, ask=4295.2)).state
    assert len(state.positions) == 1


def test_engine_rejects_unsupported_nonzero_position_finalization():
    policy = replace(policy_by_id("dubai_balanced_v1"), require_zero_positions=False)
    with pytest.raises(ValueError, match="zero positions"):
        register_signal(policy, signal_id="canal1_123", source_message_id=123,
                        direction="BUY", registered_at_utc=iso(), registered_tick_msc=100)


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
    assert result.state.positions[0].close_price == 4300.9
    assert result.state.positions[0].close_reason == "target"


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_flat_555_waiting_for_later_legs_has_no_floating_money(direction):
    policy, state = new_state("gold_now_555_v1", direction=direction, reference=4300.0)
    if direction == "BUY":
        quotes = [(4298.7, 4298.9), (4300.2, 4300.4), (4300.75, 4300.95), (4300.91, 4301.11)]
    else:
        quotes = [(4301.1, 4301.3), (4299.6, 4299.8), (4299.05, 4299.25), (4298.89, 4299.09)]
    for index, (bid, ask) in enumerate(quotes, 101):
        state = advance_tick(policy, state, tick(index, bid=bid, ask=ask)).state

    assert len(state.positions) == 1
    assert state.positions[0].status == "closed"
    assert state.status == "open"
    assert state.realized_eur == 2.0
    assert state.floating_eur == 0.0
    assert state.max_favourable_eur == 2.0
    assert state.peak_total_eur == 2.0
    later = advance_tick(policy, state, tick(110, bid=quotes[-1][0], ask=quotes[-1][1])).state
    assert later.floating_eur == 0.0
    assert later.realized_eur == 2.0


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


def test_provider_close_waits_for_causally_available_tick_and_deduplicates():
    policy, state = new_state("dubai_balanced_v1")
    state = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2)).state
    management = ShadowManagementEvent(
        event_id="m1",
        signal_id=state.signal_id,
        action="CLOSE_ALL",
        observed_at_utc=iso(2),
        observed_tick_msc=100,
        price=4400.0,
    )

    pending = apply_management(policy, state, management)
    duplicate = apply_management(policy, pending.state, management)
    historical = advance_tick(
        policy,
        duplicate.state,
        tick(102, bid=4301.0, ask=4301.2, minutes=1.9),
    )
    closed = advance_tick(
        policy,
        historical.state,
        tick(103, bid=4301.0, ask=4301.2, minutes=2.0),
    )

    assert pending.state.pending_provider_close is True
    assert pending.state.pending_provider_management == (management,)
    assert duplicate.transitions == ()
    assert historical.state.status == "open"
    assert historical.state.pending_provider_close is True
    assert historical.state.positions[0].close_price is None
    assert closed.state.exit_reason == "provider_close"
    assert closed.state.positions[0].close_price == 4301.0
    assert "pending_provider_management" not in closed.state.to_dict()


def test_provider_close_vocabulary_depends_on_declared_strategy_mode():
    dubai, dubai_state = new_state("dubai_balanced_v1")
    gold, gold_state = new_state("gold_now_555_v1", reference=4300.0)
    dubai_event = ShadowManagementEvent(
        event_id="dubai-close-first",
        signal_id=dubai_state.signal_id,
        action="CLOSE_FIRST",
        observed_at_utc=iso(1),
    )
    gold_event = ShadowManagementEvent(
        event_id="gold-close-first",
        signal_id=gold_state.signal_id,
        action="CLOSE_FIRST",
        observed_at_utc=iso(1),
    )

    dubai_result = apply_management(dubai, dubai_state, dubai_event)
    gold_result = apply_management(gold, gold_state, gold_event)

    assert dubai_result.state.pending_provider_close is True
    assert gold_result.state.pending_provider_close is False


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


def test_schema2_pending_close_without_availability_fails_incomplete():
    policy, state = new_state("dubai_balanced_v1")
    state = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2)).state
    state = replace(state, pending_provider_close=True)

    result = advance_tick(
        policy,
        state,
        tick(102, bid=4301.0, ask=4301.2, minutes=1),
    )

    assert result.state.status == "incomplete"
    assert result.state.positions[0].status == "open"
    assert result.state.positions[0].close_price is None
    assert "provider_management_availability_missing" in result.state.evidence_blockers
    assert result.transitions[-1].event == "evidence_blocker"


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
    "action, price, expected_stop, expected_reason",
    [
        ("MOVE_SL_TO_BE", None, 4300.2, "break_even"),
        ("MOVE_SL_TO_PRICE", 4298.5, 4298.5, "protective_stop"),
    ],
)
def test_explicit_provider_protection_policy_updates_open_positions(
    action,
    price,
    expected_stop,
    expected_reason,
):
    policy, _state = new_state("dubai_balanced_v1")
    policy = replace(
        policy,
        provider_protection_mode="exact",
        target_steps=(),
        trailing_distance=None,
        break_even_trigger_xau=None,
        hard_stop_eur_per_leg=None,
        basket_stop_eur=None,
        time_exit_minutes=None,
        time_exit_mode="none",
    )
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
        observed_at_utc=iso(2),
        observed_tick_msc=100,
        price=price,
    )

    pending = apply_management(policy, state, event)
    historical = advance_tick(
        policy,
        pending.state,
        tick(102, bid=expected_stop - 0.5, ask=expected_stop - 0.3, minutes=1.9),
    )
    result = advance_tick(
        policy,
        historical.state,
        tick(103, bid=expected_stop - 0.5, ask=expected_stop - 0.3, minutes=2.0),
    )

    assert pending.state.positions[0].stop_price is None
    assert pending.state.pending_provider_management == (event,)
    assert pending.transitions[0].event == "provider_protection_pending"
    assert historical.state.positions[0].status == "open"
    assert historical.state.positions[0].stop_price is None
    assert result.state.positions[0].stop_price == expected_stop
    assert result.state.positions[0].close_reason == expected_reason
    assert result.transitions[0].event == "provider_protection_applied"


def test_pending_management_round_trip_survives_restart_and_keeps_order():
    policy = replace(
        policy_by_id("dubai_balanced_v1"),
        provider_protection_mode="exact",
    )
    state = register_signal(
        policy,
        signal_id="canal1_123",
        source_message_id=123,
        direction="BUY",
        registered_at_utc=iso(),
        registered_tick_msc=100,
    )
    state = advance_tick(policy, state, tick(101, bid=4300.0, ask=4300.2)).state
    first = ShadowManagementEvent(
        event_id="m-be",
        signal_id=state.signal_id,
        action="MOVE_SL_TO_BE",
        observed_at_utc=iso(2),
        observed_tick_msc=101,
        raw_hash="a" * 64,
    )
    close = ShadowManagementEvent(
        event_id="m-close",
        signal_id=state.signal_id,
        action="CLOSE_ALL",
        observed_at_utc=iso(3),
        observed_tick_msc=102,
        raw_hash="b" * 64,
    )
    state = apply_management(policy, state, first).state
    state = apply_management(policy, state, close).state

    payload = state.to_dict()
    restored = ShadowSignalState.from_dict(payload)

    assert payload["pending_provider_management"] == [
        first.to_dict(),
        close.to_dict(),
    ]
    assert restored == state
    assert restored.state_hash == state.state_hash
    assert restored.pending_provider_management == (first, close)

    protected = advance_tick(
        policy,
        restored,
        tick(102, bid=4301.0, ask=4301.2, minutes=2.5),
    )
    closed = advance_tick(
        policy,
        protected.state,
        tick(103, bid=4301.0, ask=4301.2, minutes=3.0),
    )

    assert protected.transitions[0].event == "provider_protection_applied"
    assert protected.state.positions[0].stop_price == 4300.2
    assert protected.state.pending_provider_management == (close,)
    assert closed.state.exit_reason == "provider_close"
    assert closed.state.pending_provider_management == ()


def test_legacy_state_round_trip_omits_empty_queue_and_keeps_source_hash():
    legacy_payload = {
        "signal_id": "canal1_20700",
        "source_message_id": 20700,
        "candidate_id": "dubai_balanced_v1",
        "channel": "canal1",
        "direction": "BUY",
        "registered_at_utc": "2026-08-27T08:00:00+00:00",
        "registered_tick_msc": 100,
        "strategy_fingerprint": "",
        "execution_fingerprint": "",
        "reference_price": None,
        "status": "waiting",
        "positions": [],
        "realized_eur": 0.0,
        "floating_eur": 0.0,
        "max_favourable_eur": 0.0,
        "max_adverse_eur": 0.0,
        "profit_lock_armed": False,
        "peak_total_eur": None,
        "adverse_armed": False,
        "adverse_extreme": None,
        "pending_provider_close": False,
        "exit_reason": None,
        "last_tick_identity": None,
        "processed_management_ids": [],
        "evidence_blockers": [],
        "complete": True,
    }

    restored = ShadowSignalState.from_dict(legacy_payload)

    assert restored.to_dict() == legacy_payload
    assert "pending_provider_management" not in restored.to_dict()
    assert restored.state_hash == (
        "615413b778a351c0fa57d7f305a0cb9f1013675ff31c81b3ac89875c80795484"
    )


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
