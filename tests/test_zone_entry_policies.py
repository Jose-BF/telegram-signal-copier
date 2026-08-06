from dataclasses import FrozenInstanceError

import pytest

from zone_entry_policies import (
    default_zone_entry_policies,
    zone_policy_by_id,
)


def test_catalog_has_unique_bounded_policies():
    policies = default_zone_entry_policies()

    assert len(policies) == 9
    assert len({policy.policy_id for policy in policies}) == len(policies)
    assert all(policy.total_planned_volume <= 0.05 for policy in policies)


def test_one_plus_four_equal_spans_the_whole_zone():
    policy = zone_policy_by_id("one_plus_four_equal")

    assert policy.depth_fractions == (0.0, 0.25, 0.5, 0.75, 1.0)
    assert policy.order_modes == (
        "market",
        "limit",
        "limit",
        "limit",
        "limit",
    )
    assert policy.volumes == (0.01,) * 5
    assert policy.expiry_mode == "provider_progress"


def test_current_live_trigger_is_the_only_session_end_policy():
    policies = default_zone_entry_policies()

    session_end = [
        policy.policy_id
        for policy in policies
        if policy.expiry_mode == "session_end"
    ]
    assert session_end == ["current_live_zone_trigger"]
    baseline = zone_policy_by_id("current_live_zone_trigger")
    assert baseline.trigger_mode == "zone_touch_or_active"


def test_provider_active_policies_are_explicit_and_risk_bounded():
    all_active = zone_policy_by_id("all_provider_active")
    one_active = zone_policy_by_id("one_provider_active")

    assert all_active.trigger_mode == "provider_active"
    assert all_active.total_planned_volume == 0.05
    assert one_active.trigger_mode == "provider_active"
    assert one_active.total_planned_volume == 0.01


def test_mid_and_best_keeps_equal_total_risk_budget():
    policy = zone_policy_by_id("mid_and_best")

    assert policy.depth_fractions == (0.5, 1.0)
    assert policy.volumes == (0.025, 0.025)
    assert policy.total_planned_volume == 0.05


def test_policy_is_immutable():
    policy = zone_policy_by_id("one_first_touch")

    with pytest.raises(FrozenInstanceError):
        policy.policy_id = "changed"


def test_unknown_policy_is_rejected():
    with pytest.raises(KeyError, match="unknown zone entry policy"):
        zone_policy_by_id("missing")
