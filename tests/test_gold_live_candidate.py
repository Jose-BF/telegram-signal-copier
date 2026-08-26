from dataclasses import replace

import pytest

from gold_live_candidate import (
    CANDIDATE_FINGERPRINT,
    CANDIDATE_ID,
    GoldGuardState,
    GoldLivePolicy,
    evaluate_guard,
    favourable_move,
)


EXPECTED_FINGERPRINT = (
    "c4900550abae98de1500bf5b849072956175fdecda102fad69be9f7975cbf8d6"
)


def test_default_policy_is_the_frozen_gold_now_candidate():
    policy = GoldLivePolicy()

    assert CANDIDATE_ID == "gold_now_c490_v1"
    assert policy.fingerprint == EXPECTED_FINGERPRINT
    assert CANDIDATE_FINGERPRINT == EXPECTED_FINGERPRINT
    assert policy.live_leg_count == 5
    assert policy.live_volume_per_leg == 0.01
    assert policy.target_mode == "none"
    assert policy.partial_fraction == 0.0
    assert policy.be_mode == "price"
    assert policy.be_trigger == 12.0
    assert policy.stop_mode == "basket_money"
    assert policy.stop_value == 100.0
    assert policy.profit_lock_arm == 10.0
    assert policy.profit_lock_giveback == 8.0
    assert policy.time_exit_min == 40
    assert policy.time_exit_mode == "loss_only"
    assert policy.provider_management_mode == "ignore"


def test_research_payload_excludes_live_safety_metadata():
    assert GoldLivePolicy().research_payload() == {
        "schema_version": 1,
        "target_mode": "none",
        "target_value": None,
        "partial_fraction": 0.0,
        "runner_target": None,
        "be_mode": "price",
        "be_trigger": 12.0,
        "stop_mode": "basket_money",
        "stop_value": 100.0,
        "profit_lock_arm": 10.0,
        "profit_lock_giveback": 8.0,
        "time_exit_min": 40,
        "time_exit_mode": "loss_only",
        "provider_management_mode": "ignore",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"live_leg_count": 0},
        {"live_volume_per_leg": 0.0},
        {"be_trigger": 0.0},
        {"stop_value": 0.0},
        {"profit_lock_giveback": 10.0},
        {"time_exit_min": 0},
        {"provider_management_mode": "exact"},
    ],
)
def test_mutated_or_unsafe_policy_is_rejected(changes):
    with pytest.raises(ValueError):
        GoldLivePolicy(**changes)


def test_guard_uses_total_money_and_closes_after_eight_euro_giveback():
    policy = GoldLivePolicy()
    armed = evaluate_guard(
        policy=policy,
        state=GoldGuardState(),
        total_pl=10.0,
        n_open=5,
        elapsed_min=3.0,
        money_evidence_complete=True,
    )
    advanced = evaluate_guard(
        policy=policy,
        state=armed.state,
        total_pl=27.0,
        n_open=5,
        elapsed_min=4.0,
        money_evidence_complete=True,
    )
    closed = evaluate_guard(
        policy=policy,
        state=advanced.state,
        total_pl=19.0,
        n_open=5,
        elapsed_min=5.0,
        money_evidence_complete=True,
    )

    assert armed.action == "arm"
    assert advanced.state.peak_pl == 27.0
    assert closed.action == "close"
    assert closed.reason == "profit_lock"


def test_guard_stops_at_minus_one_hundred_and_never_on_incomplete_money():
    stopped = evaluate_guard(
        policy=GoldLivePolicy(),
        state=GoldGuardState(),
        total_pl=-100.0,
        n_open=5,
        elapsed_min=1.0,
        money_evidence_complete=True,
    )
    blocked = evaluate_guard(
        policy=GoldLivePolicy(),
        state=GoldGuardState(),
        total_pl=-150.0,
        n_open=5,
        elapsed_min=50.0,
        money_evidence_complete=False,
    )

    assert (stopped.action, stopped.reason) == ("close", "basket_stop")
    assert blocked.action == "evidence_incomplete"
    assert blocked.state == GoldGuardState()


def test_loss_only_time_exit_preserves_a_positive_basket():
    policy = GoldLivePolicy()
    losing = evaluate_guard(
        policy=policy,
        state=GoldGuardState(),
        total_pl=-0.01,
        n_open=1,
        elapsed_min=40.0,
        money_evidence_complete=True,
    )
    positive = evaluate_guard(
        policy=policy,
        state=GoldGuardState(),
        total_pl=0.01,
        n_open=1,
        elapsed_min=40.0,
        money_evidence_complete=True,
    )

    assert losing.reason == "loss_time_exit"
    assert positive.action == "none"


@pytest.mark.parametrize(
    ("direction", "entry", "exit_price", "expected"),
    [
        ("BUY", 4200.0, 4212.0, 12.0),
        ("BUY", 4200.0, 4198.0, -2.0),
        ("SELL", 4200.0, 4188.0, 12.0),
        ("SELL", 4200.0, 4202.0, -2.0),
    ],
)
def test_favourable_move_uses_the_real_exit_side(direction, entry, exit_price, expected):
    assert favourable_move(direction, entry, exit_price) == expected

