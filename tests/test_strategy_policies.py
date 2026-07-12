import pytest

import strategy_policies


def test_default_catalog_contains_controls_and_all_five_leg_allocations():
    policies = strategy_policies.default_policy_catalog()

    assert len(policies) == 22
    assert len({policy.policy_id for policy in policies}) == len(policies)
    assert policies[0].policy_id == "follow_actual"

    allocations = {
        (
            policy.close_legs,
            policy.be_legs,
            policy.runner_legs,
        )
        for policy in policies
        if policy.mode == "risk_free_allocation"
    }
    assert len(allocations) == 21
    assert all(sum(allocation) == 5 for allocation in allocations)
    assert (0, 0, 5) in allocations
    assert (0, 5, 0) in allocations
    assert (5, 0, 0) in allocations


def test_five_leg_policy_clamps_to_canal1_without_losing_runner_intent():
    policy = strategy_policies.StrategyPolicy(
        policy_id="close_1_be_3_runner_1",
        close_legs=1,
        be_legs=3,
        runner_legs=1,
    )

    assert policy.allocation_for(5) == {
        "close_now": 1,
        "move_to_be": 3,
        "runner": 1,
    }
    assert policy.allocation_for(4) == {
        "close_now": 1,
        "move_to_be": 2,
        "runner": 1,
    }


def test_all_catalog_policies_allocate_every_available_leg():
    for policy in strategy_policies.default_policy_catalog():
        for leg_count in (1, 4, 5):
            allocation = policy.allocation_for(leg_count)
            assert sum(allocation.values()) == leg_count
            assert all(value >= 0 for value in allocation.values())


def test_invalid_policy_allocation_is_rejected():
    with pytest.raises(ValueError, match="sum to base_leg_count"):
        strategy_policies.StrategyPolicy(
            policy_id="invalid",
            close_legs=1,
            be_legs=1,
            runner_legs=1,
        )
