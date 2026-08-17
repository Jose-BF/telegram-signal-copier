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


def test_genome_rejects_incompatible_profit_lock():
    candidate = StrategyGenome.baseline().with_change(
        profit_lock_arm=2.0,
        profit_lock_giveback=None,
    )

    assert "incomplete_profit_lock" in candidate.validation_errors()


def test_lineage_does_not_change_strategy_fingerprint():
    baseline = StrategyGenome.baseline()
    child = baseline.with_lineage(
        parent_fingerprints=(baseline.fingerprint,),
        mutation_reason="profit_given_back",
        lineage_depth=1,
    )

    assert child.fingerprint == baseline.fingerprint
