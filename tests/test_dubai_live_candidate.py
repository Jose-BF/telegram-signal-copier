from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from dubai_live_candidate import (
    CANDIDATE_FINGERPRINT,
    DubaiGuardState,
    DubaiLivePolicy,
    evaluate_guard,
)


EXPECTED_FINGERPRINT = (
    "32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631"
)


def test_default_policy_is_the_frozen_research_candidate():
    policy = DubaiLivePolicy()

    assert policy.fingerprint == EXPECTED_FINGERPRINT
    assert CANDIDATE_FINGERPRINT == EXPECTED_FINGERPRINT
    assert policy.volume_weights == (0.01, 0.04, 0.04)
    assert policy.entry_ladder_step == 4.0
    assert policy.entry_expiry_min == 15
    assert policy.stop_value == 25.0
    assert policy.profit_lock_arm == 10.0
    assert policy.profit_lock_giveback == 2.0
    assert policy.time_exit_min == 40
    assert policy.time_exit_mode == "loss_only"
    assert policy.target_mode == "none"
    assert policy.be_mode == "none"
    assert policy.provider_management_mode == "exact"


def test_policy_payload_matches_the_research_fingerprint_contract():
    payload = DubaiLivePolicy().research_payload()

    assert payload == {
        "schema_version": 1,
        "entry_mode": "signal_market",
        "entry_value": None,
        "entry_confirmation_value": None,
        "entry_expiry_min": 15,
        "entry_ladder_mode": "adverse",
        "entry_ladder_step": 4.0,
        "leg_count": 3,
        "volume_weights": [0.01, 0.04, 0.04],
        "target_mode": "none",
        "target_value": None,
        "partial_fraction": 0.0,
        "runner_target": None,
        "be_mode": "none",
        "be_trigger": None,
        "stop_mode": "basket_money",
        "stop_value": 25.0,
        "profit_lock_arm": 10.0,
        "profit_lock_giveback": 2.0,
        "time_exit_min": 40,
        "time_exit_mode": "loss_only",
        "provider_management_mode": "exact",
        "context_filter_mode": "none",
        "context_filter_value": None,
    }


def test_live_policy_matches_the_frozen_research_preregistration():
    preregistration_path = (
        Path(__file__).parents[1]
        / "research"
        / "preregistrations"
        / "dubai_20260822.json"
    )
    preregistration = json.loads(
        preregistration_path.read_text(encoding="utf-8")
    )
    candidate = preregistration["primary_full_candidate"]
    policy = DubaiLivePolicy()

    assert candidate["fingerprint"] == policy.fingerprint
    assert candidate["rule"] == {
        key: value
        for key, value in policy.research_payload().items()
        if key in candidate["rule"]
    }


@pytest.mark.parametrize(
    ("direction", "expected_levels"),
    [
        ("BUY", [None, 4196.0, 4192.0]),
        ("SELL", [None, 4204.0, 4208.0]),
    ],
)
def test_entry_plan_uses_the_real_first_fill_as_adverse_anchor(
    direction,
    expected_levels,
):
    opened_at = datetime(2026, 8, 23, 9, 30, 0)

    legs = DubaiLivePolicy().entry_plan(
        direction=direction,
        anchor_price=4200.0,
        opened_at=opened_at,
    )

    assert [leg.volume for leg in legs] == [0.01, 0.04, 0.04]
    assert [leg.trigger_price for leg in legs] == expected_levels
    assert legs[0].opened_immediately is True
    assert all(leg.expires_at == opened_at + timedelta(minutes=15) for leg in legs)


@pytest.mark.parametrize(
    "changes",
    [
        {"volume_weights": (0.01, 0.04)},
        {"entry_ladder_step": 0.0},
        {"entry_expiry_min": 0},
        {"stop_value": -25.0},
        {"profit_lock_giveback": 0.0},
        {"profit_lock_arm": -1.0},
        {"time_exit_min": 0},
    ],
)
def test_invalid_or_mutated_live_contract_is_rejected(changes):
    with pytest.raises(ValueError):
        DubaiLivePolicy(**changes)


def test_guard_stops_the_basket_before_any_other_rule():
    decision = evaluate_guard(
        policy=DubaiLivePolicy(),
        state=DubaiGuardState(armed=True, peak_pl=20.0),
        total_pl=-25.01,
        n_open=3,
        elapsed_min=45.0,
        money_evidence_complete=True,
    )

    assert decision.action == "close"
    assert decision.reason == "basket_stop"


def test_guard_arms_at_ten_and_closes_two_euros_below_the_peak():
    armed = evaluate_guard(
        policy=DubaiLivePolicy(),
        state=DubaiGuardState(),
        total_pl=10.0,
        n_open=3,
        elapsed_min=5.0,
        money_evidence_complete=True,
    )
    advanced = evaluate_guard(
        policy=DubaiLivePolicy(),
        state=armed.state,
        total_pl=17.25,
        n_open=3,
        elapsed_min=10.0,
        money_evidence_complete=True,
    )
    closed = evaluate_guard(
        policy=DubaiLivePolicy(),
        state=advanced.state,
        total_pl=15.24,
        n_open=3,
        elapsed_min=11.0,
        money_evidence_complete=True,
    )

    assert armed.action == "arm"
    assert advanced.action == "none"
    assert advanced.state.peak_pl == 17.25
    assert closed.action == "close"
    assert closed.reason == "profit_lock"


def test_loss_only_time_exit_closes_non_positive_basket_after_forty_minutes():
    losing = evaluate_guard(
        policy=DubaiLivePolicy(),
        state=DubaiGuardState(),
        total_pl=-0.01,
        n_open=1,
        elapsed_min=40.0,
        money_evidence_complete=True,
    )
    positive = evaluate_guard(
        policy=DubaiLivePolicy(),
        state=DubaiGuardState(),
        total_pl=0.01,
        n_open=1,
        elapsed_min=40.0,
        money_evidence_complete=True,
    )

    assert losing.action == "close"
    assert losing.reason == "loss_time_exit"
    assert positive.action == "none"


def test_incomplete_money_evidence_cannot_arm_or_time_close():
    decision = evaluate_guard(
        policy=DubaiLivePolicy(),
        state=DubaiGuardState(),
        total_pl=15.0,
        n_open=3,
        elapsed_min=45.0,
        money_evidence_complete=False,
    )

    assert decision.action == "evidence_incomplete"
    assert decision.reason == "money_evidence_incomplete"
    assert decision.state == DubaiGuardState()


def test_incomplete_money_evidence_cannot_trigger_the_aggregate_stop():
    decision = evaluate_guard(
        policy=DubaiLivePolicy(),
        state=DubaiGuardState(),
        total_pl=-30.0,
        n_open=2,
        elapsed_min=5.0,
        money_evidence_complete=False,
    )

    assert decision.action == "evidence_incomplete"
    assert decision.reason == "money_evidence_incomplete"
    assert decision.state == DubaiGuardState()
