from __future__ import annotations

import pytest

from research.dubai_iterative.contracts import (
    SearchBudget,
    SearchSpace,
    StrategyGenome,
)


def test_genome_fingerprint_is_stable_after_round_trip():
    first = StrategyGenome.baseline().with_change(
        target_mode="fixed_basket",
        target_value=2.0,
    )
    second = StrategyGenome.from_dict(first.to_dict())

    assert first.fingerprint == second.fingerprint


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"generation": 50}, "max_generations"),
        ({"evaluations": 1_000_000}, "max_evaluations"),
        ({"elapsed_seconds": 7_200}, "max_wall_seconds"),
        ({"stale_generations": 8}, "no_improvement"),
        ({"deepest_lineage": 12}, "max_lineage_depth"),
    ],
)
def test_budget_stops_on_every_hard_limit(changes, expected):
    budget = SearchBudget()
    state = {
        "generation": 1,
        "evaluations": 10,
        "elapsed_seconds": 1.0,
        "stale_generations": 0,
        "deepest_lineage": 1,
    }
    state.update(changes)

    assert budget.stop_reason(**state) == expected


def test_budget_does_not_stop_before_a_limit():
    assert SearchBudget().stop_reason(
        generation=49,
        evaluations=999_999,
        elapsed_seconds=7_199,
        stale_generations=7,
        deepest_lineage=11,
    ) is None


def test_budget_rejects_non_positive_limits():
    with pytest.raises(ValueError, match="max_generations"):
        SearchBudget(max_generations=0)


def test_genome_allows_more_than_observed_dubai_exposure():
    candidate = StrategyGenome.baseline().with_change(
        leg_count=6,
        volume_weights=(0.02, 0.02, 0.02, 0.02, 0.02, 0.02),
    )

    assert candidate.validation_errors() == ()


def test_search_space_exposure_bound_is_explicit_and_configurable():
    candidate = StrategyGenome.baseline().with_change(
        leg_count=6,
        volume_weights=(0.05, 0.05, 0.05, 0.05, 0.05, 0.05),
    )

    assert SearchSpace(max_total_volume=0.20).validation_errors(candidate) == (
        "outside_search_volume",
    )
    assert SearchSpace(max_total_volume=0.50).validation_errors(candidate) == ()


def test_search_space_rejects_unexecutable_partial_volume():
    candidate = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.01,),
        target_mode="partial_runner",
        target_value=2.0,
        partial_fraction=0.5,
        runner_target=6.0,
    )

    assert "unexecutable_partial_volume" in SearchSpace().validation_errors(candidate)


def test_search_space_allows_larger_explicit_research_envelopes():
    candidate = StrategyGenome.baseline().with_change(
        leg_count=4,
        volume_weights=(0.25, 0.25, 0.25, 0.25),
    )

    assert SearchSpace(max_total_volume=1.0).validation_errors(candidate) == ()


def test_search_space_rejects_strategies_beyond_the_loaded_time_horizon():
    space = SearchSpace(
        max_entry_expiry_min=60,
        max_time_exit_min=240,
        max_path_horizon_min=240,
    )

    late_entry = StrategyGenome.baseline().with_change(entry_expiry_min=61)
    late_exit = StrategyGenome.baseline().with_change(time_exit_min=241)
    late_counterfactual = StrategyGenome.baseline().with_change(
        entry_mode="pullback",
        entry_value=1.0,
        entry_expiry_min=60,
        time_exit_min=181,
    )
    observed_full_horizon = StrategyGenome.baseline().with_change(
        time_exit_min=240,
    )

    assert "outside_search_entry_expiry" in space.validation_errors(late_entry)
    assert "outside_search_time_exit" in space.validation_errors(late_exit)
    assert "outside_loaded_path_horizon" in space.validation_errors(late_counterfactual)
    assert "outside_loaded_path_horizon" not in space.validation_errors(observed_full_horizon)


def test_entry_ladders_require_multiple_legs_and_a_positive_price_step():
    missing_step = StrategyGenome.baseline().with_change(
        entry_ladder_mode="adverse",
    )
    one_leg = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.01,),
        entry_ladder_mode="favourable",
        entry_ladder_step=1.0,
    )
    valid = StrategyGenome.baseline().with_change(
        entry_ladder_mode="adverse",
        entry_ladder_step=1.0,
    )

    assert "missing_entry_ladder_step" in missing_step.validation_errors()
    assert "entry_ladder_requires_multiple_legs" in one_leg.validation_errors()
    assert valid.validation_errors() == ()


def test_fixed_move_target_requires_a_positive_price_distance():
    missing = StrategyGenome.baseline().with_change(
        target_mode="fixed_move",
    )
    valid = StrategyGenome.baseline().with_change(
        target_mode="fixed_move",
        target_value=2.5,
    )

    assert "missing_target_value" in missing.validation_errors()
    assert valid.validation_errors() == ()


def test_genome_rejects_incompatible_profit_lock():
    candidate = StrategyGenome.baseline().with_change(
        profit_lock_arm=2.0,
        profit_lock_giveback=None,
    )

    assert "incomplete_profit_lock" in candidate.validation_errors()


def test_genome_rejects_profit_lock_that_fixed_basket_target_preempts():
    unreachable = StrategyGenome.baseline().with_change(
        target_mode="fixed_basket",
        target_value=2.0,
        profit_lock_arm=3.0,
        profit_lock_giveback=1.0,
    )
    reachable = unreachable.with_change(profit_lock_arm=1.0)

    assert "unreachable_profit_lock" in unreachable.validation_errors()
    assert "unreachable_profit_lock" not in reachable.validation_errors()


def test_lineage_does_not_change_strategy_fingerprint():
    baseline = StrategyGenome.baseline()
    child = baseline.with_lineage(
        parent_fingerprints=(baseline.fingerprint,),
        mutation_reason="profit_given_back",
        lineage_depth=1,
    )

    assert child.fingerprint == baseline.fingerprint
