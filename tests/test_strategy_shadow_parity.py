from __future__ import annotations

from datetime import datetime, timedelta, timezone

import dubai_live_candidate
import gold_555_live_candidate
import gold_live_candidate
from strategy_shadow_catalog import policy_by_id
from strategy_shadow_contracts import ShadowTick
from strategy_shadow_engine import advance_tick, register_signal


BASE = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)


def _tick(
    index: int,
    *,
    bid: float,
    ask: float,
    minutes: float,
) -> ShadowTick:
    observed = BASE + timedelta(minutes=minutes)
    return ShadowTick(
        time_msc=int(observed.timestamp() * 1000) + index,
        bid=bid,
        ask=ask,
        observed_at_utc=observed.isoformat(),
        positive_eur_per_move_lot=100.0,
        negative_eur_per_move_lot=100.0,
        money_evidence_id="identity-eur",
    )


def _state(candidate_id: str, *, direction: str = "BUY", reference=None):
    policy = policy_by_id(candidate_id)
    state = register_signal(
        policy,
        signal_id=f"{policy.channel}_golden",
        source_message_id=9001,
        direction=direction,
        registered_at_utc=BASE.isoformat(),
        registered_tick_msc=int(BASE.timestamp() * 1000),
        reference_price=reference,
    )
    return policy, state


def test_frozen_controls_match_the_independent_live_policy_contracts():
    dubai = policy_by_id("dubai_balanced_v1")
    dubai_live = dubai_live_candidate.DubaiLivePolicy()
    assert dubai.strategy_fingerprint == dubai_live_candidate.CANDIDATE_FINGERPRINT
    assert dubai.entry_volumes == dubai_live.volume_weights
    assert dubai.ladder_step == dubai_live.entry_ladder_step
    assert dubai.ladder_expiry_minutes == dubai_live.entry_expiry_min
    assert dubai.basket_stop_eur == dubai_live.stop_value
    assert dubai.profit_arm_eur == dubai_live.profit_lock_arm
    assert dubai.profit_giveback_eur == dubai_live.profit_lock_giveback
    assert dubai.time_exit_minutes == dubai_live.time_exit_min

    gold_555 = policy_by_id("gold_now_555_v1")
    live_555 = gold_555_live_candidate.Gold555Policy()
    assert gold_555.strategy_fingerprint == gold_555_live_candidate.CANDIDATE_FINGERPRINT
    assert gold_555.entry_volumes == live_555.entry_volumes
    assert gold_555.entry_adverse == live_555.entry_adverse
    assert gold_555.entry_reversal == live_555.entry_reversal
    assert gold_555.ladder_step == live_555.ladder_step
    assert gold_555.target_steps == live_555.target_steps
    assert gold_555.trailing_distance == live_555.trailing_distance

    gold_c490 = policy_by_id("gold_now_c490_v1")
    live_c490 = gold_live_candidate.GoldLivePolicy()
    assert gold_c490.strategy_fingerprint == gold_live_candidate.CANDIDATE_FINGERPRINT
    assert gold_c490.entry_volumes == (
        live_c490.live_volume_per_leg,
    ) * live_c490.live_leg_count
    assert gold_c490.hard_stop_eur_per_leg == live_c490.broker_loss_budget_per_leg
    assert gold_c490.break_even_trigger_xau == live_c490.be_trigger
    assert gold_c490.basket_stop_eur == live_c490.stop_value


def test_dubai_shadow_guard_matches_independent_live_guard_golden_path():
    policy, state = _state("dubai_balanced_v1")
    state = advance_tick(
        policy, state, _tick(1, bid=4300.0, ask=4300.2, minutes=0.01),
    ).state
    armed = advance_tick(
        policy, state, _tick(2, bid=4310.2, ask=4310.4, minutes=1),
    ).state
    closed = advance_tick(
        policy, armed, _tick(3, bid=4307.9, ask=4308.1, minutes=2),
    ).state

    live_policy = dubai_live_candidate.DubaiLivePolicy()
    live_state = dubai_live_candidate.DubaiGuardState()
    live_armed = dubai_live_candidate.evaluate_guard(
        policy=live_policy,
        state=live_state,
        total_pl=armed.floating_eur,
        n_open=1,
        elapsed_min=1,
        money_evidence_complete=True,
    )
    live_closed = dubai_live_candidate.evaluate_guard(
        policy=live_policy,
        state=live_armed.state,
        total_pl=closed.realized_eur,
        n_open=1,
        elapsed_min=2,
        money_evidence_complete=True,
    )

    assert live_armed.action == "arm"
    assert armed.profit_lock_armed is True
    assert live_closed.reason == "profit_lock"
    assert closed.exit_reason == "profit_giveback"
    assert closed.realized_eur == live_closed.observed_pl


def test_c490_shadow_guard_matches_independent_live_guard_golden_path():
    policy, state = _state("gold_now_c490_v1")
    state = advance_tick(
        policy, state, _tick(1, bid=4300.0, ask=4300.2, minutes=0.01),
    ).state
    armed = advance_tick(
        policy, state, _tick(2, bid=4302.2, ask=4302.4, minutes=1),
    ).state
    closed = advance_tick(
        policy, armed, _tick(3, bid=4300.5, ask=4300.7, minutes=2),
    ).state

    live_policy = gold_live_candidate.GoldLivePolicy()
    live_state = gold_live_candidate.GoldGuardState()
    live_armed = gold_live_candidate.evaluate_guard(
        policy=live_policy,
        state=live_state,
        total_pl=armed.floating_eur,
        n_open=5,
        elapsed_min=1,
        money_evidence_complete=True,
    )
    live_closed = gold_live_candidate.evaluate_guard(
        policy=live_policy,
        state=live_armed.state,
        total_pl=closed.realized_eur,
        n_open=5,
        elapsed_min=2,
        money_evidence_complete=True,
    )

    assert live_armed.action == "arm"
    assert armed.profit_lock_armed is True
    assert live_closed.reason == "profit_lock"
    assert closed.exit_reason == "profit_giveback"
    assert closed.realized_eur == live_closed.observed_pl


def test_555_shadow_fill_levels_match_independent_live_price_oracle():
    policy, state = _state("gold_now_555_v1", reference=4300.0)
    state = advance_tick(
        policy, state, _tick(1, bid=4298.7, ask=4298.9, minutes=1),
    ).state
    state = advance_tick(
        policy, state, _tick(2, bid=4300.2, ask=4300.4, minutes=2),
    ).state

    position = state.positions[0]
    live = gold_555_live_candidate.Gold555Policy()
    assert position.entry_price == 4300.4
    assert position.target_price == live.target_price("BUY", 4300.4, 0)
    assert position.stop_price == live.initial_stop("BUY", 4300.4)
