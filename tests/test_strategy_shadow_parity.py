from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import dubai_live_candidate
import gold_555_live_candidate
import gold_live_candidate
import position_lifecycle_monitor as live_monitor
from state import Signal
from strategy_shadow_catalog import policy_by_id
from strategy_shadow_contracts import ShadowPosition, ShadowSignalState, ShadowTick
from strategy_shadow_engine import advance_tick, register_signal
from strategy_shadow_parity import (
    actual_logic_signature,
    compare_logic_signatures,
    shadow_logic_signature,
)


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


@pytest.mark.asyncio
async def test_555_live_and_shadow_both_keep_ladder_after_flat_first_tp(
        monkeypatch):
    policy, shadow = _state(
        "gold_now_555_v1",
        reference=4300.0,
    )
    shadow = advance_tick(
        policy, shadow, _tick(1, bid=4298.7, ask=4298.9, minutes=1),
    ).state
    shadow = advance_tick(
        policy, shadow, _tick(2, bid=4300.2, ask=4300.4, minutes=2),
    ).state
    shadow = advance_tick(
        policy, shadow, _tick(3, bid=4300.9, ask=4301.1, minutes=3),
    ).state

    assert shadow.status == "open"
    assert not any(position.status == "open" for position in shadow.positions)

    shadow = advance_tick(
        policy, shadow, _tick(4, bid=4298.6, ask=4298.8, minutes=4),
    ).state
    assert len(shadow.positions) == 2
    assert shadow.positions[1].status == "open"

    live_policy = gold_555_live_candidate.Gold555Policy()
    first_fill_at = datetime(2026, 8, 27, 8, 2)
    entry_levels = live_policy.entry_levels("BUY", 4300.4)
    live = Signal(
        channel="canal2",
        message_id=9001,
        direction="BUY",
        timestamp=first_fill_at,
        market_ticket=1000,
        market_fill_price=4300.4,
        live_strategy_id=gold_555_live_candidate.CANDIDATE_ID,
        live_strategy_fingerprint=(
            gold_555_live_candidate.CANDIDATE_FINGERPRINT
        ),
        candidate_entry_anchor=4300.4,
        candidate_first_fill_at=first_fill_at,
        candidate_entry_expires_at=first_fill_at + timedelta(minutes=30),
        candidate_entry_legs=[
            {
                "index": index,
                "volume": live_policy.entry_volumes[index],
                "trigger_price": entry_levels[index],
                "target_step": live_policy.target_steps[index],
            }
            for index in range(len(live_policy.entry_volumes))
        ],
    )
    live.candidate_entry_prices_by_ticket[1000] = 4300.4
    live.candidate_hard_stops[1000] = live_policy.initial_stop(
        "BUY", 4300.4
    )

    assert live_monitor._should_auto_finalize_signal(
        live,
        {"positions_complete": True, "n_open": 0},
        monitor_started_monotonic=100.0,
        now_monotonic=131.0,
        now=first_fill_at + timedelta(minutes=3),
    ) is False

    async def fake_open(_signal, _leg, _observed_price):
        return 1001, 4298.8

    monkeypatch.setattr(live_monitor, "_open_candidate_leg", fake_open)
    monkeypatch.setattr(
        live_monitor,
        "_queue_gold_555_leg_protection",
        lambda *args, **kwargs: (4268.8, 4299.8),
    )
    monkeypatch.setattr(
        live_monitor,
        "_journal_event",
        lambda *args, **kwargs: None,
    )

    opened = await live_monitor._process_candidate_entry_tick(
        live,
        SimpleNamespace(bid=4298.6, ask=4298.8, time_msc=4),
        now=first_fill_at + timedelta(minutes=4),
    )

    assert opened == 1
    assert live.candidate_filled_leg_indexes == [1]
    assert live.dca_tickets == [1001]


def _structural_gold_state():
    policy = policy_by_id("gold_now_555_v1")
    state = ShadowSignalState.new(
        signal_id="canal2_5000",
        source_message_id=5000,
        candidate_id=policy.candidate_id,
        channel="canal2",
        direction="BUY",
        registered_at_utc="2026-08-31T08:00:00+00:00",
        registered_tick_msc=1,
        strategy_fingerprint=policy.strategy_fingerprint,
        execution_fingerprint=policy.execution_fingerprint,
    )
    positions = (
        ShadowPosition(
            leg_index=0,
            volume=0.04,
            entry_price=100.20,
            opened_tick_msc=2,
            target_price=100.70,
            stop_price=70.20,
            status="closed",
            close_price=100.70,
            closed_tick_msc=3,
            close_reason="target",
            realized_eur=1.72,
        ),
        ShadowPosition(
            leg_index=1,
            volume=0.03,
            entry_price=98.70,
            opened_tick_msc=4,
            target_price=99.70,
            stop_price=68.70,
            status="closed",
            close_price=99.70,
            closed_tick_msc=5,
            close_reason="target",
            realized_eur=2.58,
        ),
    )
    return policy, replace(
        state,
        status="closed",
        positions=positions,
        exit_reason="target",
    )


def _structural_gold_ledger_source(*, second_target=199.90):
    policy = policy_by_id("gold_now_555_v1")
    return {
        "direction": "BUY",
        "strategy_snapshot": {
            "live_strategy_id": policy.candidate_id,
            "live_strategy_fingerprint": policy.strategy_fingerprint,
            "code_commit": "a" * 40,
        },
        "positions": [
            {
                "role": "market_a",
                "volume": 0.04,
                "open_price": 200.10,
                "is_closed": True,
                "close_reason": "tp",
                "open_deal": {"comment": "c2_5000_g55"},
                "tp_history": [{"status": "confirmed", "tp": 200.60}],
                "sl_history": [{"status": "confirmed", "sl": 170.10}],
            },
            {
                "role": "scale_out_leg",
                "volume": 0.03,
                "open_price": 198.90,
                "is_closed": True,
                "close_reason": "tp",
                "open_deal": {"comment": "c2_5000_B1_g55"},
                "tp_history": [
                    {"status": "confirmed", "tp": second_target}
                ],
                "sl_history": [{"status": "confirmed", "sl": 168.90}],
            },
        ],
    }


def test_logic_parity_ignores_fill_price_but_preserves_relative_targets():
    policy, state = _structural_gold_state()

    shadow = shadow_logic_signature(state, policy)
    actual, blockers = actual_logic_signature(
        _structural_gold_ledger_source(), policy
    )
    comparison = compare_logic_signatures(actual, shadow)

    assert blockers == ()
    assert comparison["match"] is True
    assert comparison["differences"] == []


def test_logic_parity_detects_wrong_target_assignment():
    policy, state = _structural_gold_state()

    shadow = shadow_logic_signature(state, policy)
    actual, blockers = actual_logic_signature(
        _structural_gold_ledger_source(second_target=200.40), policy
    )
    comparison = compare_logic_signatures(actual, shadow)

    assert blockers == ()
    assert comparison["match"] is False
    assert any(
        "positions[1].target" in item
        for item in comparison["differences"]
    )


def test_actual_signature_refuses_ambiguous_leg_identity():
    policy, _state = _structural_gold_state()
    source = _structural_gold_ledger_source()
    source["positions"][1]["open_deal"]["comment"] = "unknown"

    signature, blockers = actual_logic_signature(source, policy)

    assert signature is None
    assert blockers == ("actual_leg_identity_ambiguous",)


def test_actual_signature_handles_malformed_open_deal_without_crashing():
    policy, _state = _structural_gold_state()
    source = _structural_gold_ledger_source()
    source["positions"][1]["open_deal"] = "malformed"

    signature, blockers = actual_logic_signature(source, policy)

    assert signature is None
    assert blockers == ("actual_leg_identity_ambiguous",)


def test_logic_parity_normalizes_mt5_be_exit_reason():
    policy, state = _structural_gold_state()
    positions = list(state.positions)
    positions[0] = replace(positions[0], close_reason="break_even")
    state = replace(state, positions=tuple(positions))
    source = _structural_gold_ledger_source()
    source["positions"][0]["close_reason"] = "be"

    comparison = compare_logic_signatures(
        actual_logic_signature(source, policy)[0],
        shadow_logic_signature(state, policy),
    )

    assert comparison["match"] is True


def test_logic_parity_rejects_wrong_relative_stop_level():
    policy, state = _structural_gold_state()
    source = _structural_gold_ledger_source()
    source["positions"][0]["sl_history"] = [
        {"status": "confirmed", "sl": 190.10}
    ]

    comparison = compare_logic_signatures(
        actual_logic_signature(source, policy)[0],
        shadow_logic_signature(state, policy),
    )

    assert comparison["match"] is False
    assert any(
        "positions[0].protection" in item
        for item in comparison["differences"]
    )
