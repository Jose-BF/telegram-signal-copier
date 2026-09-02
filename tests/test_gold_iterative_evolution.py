from __future__ import annotations

from decimal import Decimal

from research.dubai_iterative.contracts import SearchBudget, SearchSpace
from research.dubai_iterative.engine import SimulationResult
from research.dubai_iterative.evolution import (
    CandidateEvaluation,
    collapse_observational_equivalents,
    diagnose_gold,
    mutate_gold_from_diagnosis,
)
from research.gold_iterative.contracts import gold_555_genome, gold_c490_genome
from research.gold_iterative.seeds import (
    gold_parameter_neighborhood,
    gold_seed_population,
    sample_gold_population,
)


SPACE = SearchSpace(
    min_total_volume=0.01,
    max_total_volume=0.40,
    max_legs=8,
    max_entry_expiry_min=240,
    max_time_exit_min=240,
    max_path_horizon_min=480,
)


def _result(
    signal_id: str,
    *,
    pnl: str = "0.00",
    favourable: str = "0.00",
    adverse: str = "0.00",
    drawdown: str = "0.00",
    reason: str = "not_filled",
    unfilled: bool = False,
    filled_volume: float = 0.0,
) -> SimulationResult:
    return SimulationResult(
        signal_id=signal_id,
        strategy_fingerprint="test",
        confidence_layer="counterfactual_entry",
        entries=(),
        exits=(),
        pnl_eur=Decimal(pnl),
        exit_reason=reason,
        max_favourable_eur=Decimal(favourable),
        max_adverse_eur=Decimal(adverse),
        max_floating_drawdown_eur=Decimal(drawdown),
        max_favourable_move=0.0,
        max_adverse_move=0.0,
        blockers=(),
        last_tick_index=10,
        unfilled=unfilled,
        filled_volume=filled_volume,
    )


def _evaluation(genome, *results) -> CandidateEvaluation:
    return CandidateEvaluation.from_results(
        genome,
        tuple(("2026-08-31", result) for result in results),
    )


def test_gold_generation_zero_contains_materially_distinct_families() -> None:
    first = gold_seed_population(SPACE, seed=20260902)
    second = gold_seed_population(SPACE, seed=20260902)
    families = {item.mutation_reason for item in first}

    assert [item.fingerprint for item in first] == [
        item.fingerprint for item in second
    ]
    assert {
        "seed:no_entry_control",
        "seed:provider_baseline",
        "seed:immediate_scale_out",
        "seed:adverse_ladder",
        "seed:adverse_reversal",
        "seed:partial_runner",
        "seed:basket_capture",
        "seed:staged_protection",
        "seed:short_hold",
        "seed:long_hold",
        "seed:gold_555",
        "seed:gold_c490",
    } <= families
    assert {item.entry_mode for item in first} >= {
        "no_entry",
        "signal_market",
        "adverse_reversal",
    }
    assert len({item.fingerprint for item in first}) == len(first)
    assert all(item.schema_version == 2 for item in first)
    assert all(not item.validation_errors() for item in first)
    assert all(not SPACE.validation_errors(item) for item in first)


def test_gold_neighborhood_changes_every_schema_v2_strategy_block() -> None:
    parent = gold_555_genome()

    children = gold_parameter_neighborhood(parent, SPACE)

    assert 20 < len(children) < 2_000
    assert len({item.fingerprint for item in children}) == len(children)
    assert all(not item.validation_errors() for item in children)
    assert all(not SPACE.validation_errors(item) for item in children)
    assert any(item.entry_mode == "signal_market" for item in children)
    assert any(item.entry_confirmation_value != parent.entry_confirmation_value for item in children)
    assert any(item.target_steps != parent.target_steps for item in children)
    assert any(item.trailing_distance != parent.trailing_distance for item in children)
    assert any(item.hard_stop_eur_per_leg is not None for item in children)
    assert any(item.time_exit_mode != parent.time_exit_mode for item in children)
    assert any(item.provider_management_mode == "ignore" for item in children)
    assert any(item.pending_entry_policy == "none" for item in children)
    assert all(item.entry_mode != "actual_mt5" for item in children)


def test_gold_scouts_never_use_missing_actual_mt5_entry_evidence() -> None:
    scouts = sample_gold_population(SPACE, seed=20260902, count=128)

    assert len(scouts) == 128
    assert all(item.entry_mode != "actual_mt5" for item in scouts)


def test_gold_critic_relaxes_an_entry_filter_that_skips_every_signal() -> None:
    parent = gold_555_genome()
    evaluation = _evaluation(
        parent,
        _result("canal2_1", unfilled=True),
        _result("canal2_2", unfilled=True),
    )

    diagnosis = diagnose_gold(evaluation)
    children = mutate_gold_from_diagnosis(
        parent,
        diagnosis,
        search_space=SPACE,
        seed=7,
    )

    assert "entry_filter_too_strict" in diagnosis.labels
    assert children
    assert any(item.entry_mode == "signal_market" for item in children)
    assert all(item.entry_mode != "actual_mt5" for item in children)
    assert all(item.parent_fingerprints == (parent.fingerprint,) for item in children)
    assert all(item.lineage_depth == 1 for item in children)


def test_gold_critic_mutates_trailing_and_hard_stop_as_separate_causes() -> None:
    parent = gold_c490_genome()
    evaluation = _evaluation(
        parent,
        _result(
            "canal2_10",
            pnl="-20.00",
            favourable="12.00",
            adverse="-20.00",
            drawdown="32.00",
            reason="hard_stop_per_leg",
            filled_volume=0.05,
        ),
        _result(
            "canal2_11",
            pnl="2.00",
            favourable="15.00",
            adverse="-2.00",
            drawdown="13.00",
            reason="trailing_stop",
            filled_volume=0.05,
        ),
    )

    diagnosis = diagnose_gold(evaluation)
    children = mutate_gold_from_diagnosis(
        parent,
        diagnosis,
        search_space=SPACE,
        seed=9,
    )

    assert {"per_leg_stop_pressure", "trailing_giveback"} <= set(
        diagnosis.labels
    )
    assert any(item.mutation_reason == "per_leg_stop_pressure" for item in children)
    assert any(item.mutation_reason == "trailing_giveback" for item in children)


def test_observational_equivalence_keeps_one_representative() -> None:
    simple = gold_c490_genome()
    complex_genome = simple.with_change(
        context_filter_mode="max_spread",
        context_filter_value=5.0,
    )
    shared = _result(
        "canal2_same",
        pnl="4.00",
        favourable="6.00",
        adverse="-1.00",
        drawdown="2.00",
        reason="profit_lock",
        filled_volume=0.05,
    )

    collapsed = collapse_observational_equivalents(
        (_evaluation(complex_genome, shared), _evaluation(simple, shared))
    )

    assert len(collapsed) == 1
    assert collapsed[0].genome.fingerprint == simple.fingerprint


def test_search_budget_has_independent_hard_anti_loop_stops() -> None:
    budget = SearchBudget(
        max_generations=20,
        max_evaluations=1_000,
        max_wall_seconds=60,
        patience_generations=3,
        max_lineage_depth=5,
    )

    assert budget.stop_reason(
        generation=2,
        evaluations=10,
        elapsed_seconds=2,
        stale_generations=3,
        deepest_lineage=2,
    ) == "no_improvement"
    assert budget.stop_reason(
        generation=2,
        evaluations=10,
        elapsed_seconds=2,
        stale_generations=0,
        deepest_lineage=5,
    ) == "max_lineage_depth"
