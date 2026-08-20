from dataclasses import replace
from decimal import Decimal

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import SimulationResult
from research.dubai_iterative.evolution import CandidateEvaluation
from research.dubai_iterative.robustness import (
    ScenarioEvaluation,
    assess_execution_robustness,
    assess_robust_daily_stability,
    group_observationally_equivalent,
    rank_observational_groups,
    rank_robust_candidates,
)
from research.dubai_iterative.search import ChronologicalFold


def _result(signal_id: str, pnl: str, *, unfilled: bool = False):
    value = Decimal(pnl)
    return SimulationResult(
        signal_id=signal_id,
        strategy_fingerprint="strategy",
        confidence_layer="counterfactual_entry",
        entries=(),
        exits=(),
        pnl_eur=value,
        exit_reason="not_filled" if unfilled else "test",
        max_favourable_eur=max(value, Decimal("0")),
        max_adverse_eur=min(value, Decimal("0")),
        max_floating_drawdown_eur=max(-value, Decimal("0")),
        max_favourable_move=0.0,
        max_adverse_move=0.0,
        blockers=(),
        last_tick_index=0,
        unfilled=unfilled,
        filled_volume=0.0 if unfilled else 0.01,
    )


def _evaluation(genome, values):
    return CandidateEvaluation.from_results(
        genome,
        (
            (day, _result(f"signal_{index}", pnl, unfilled=unfilled))
            for index, (day, pnl, unfilled) in enumerate(values)
        ),
    )


def test_execution_robustness_rejects_a_latency_dependent_false_winner():
    genome = StrategyGenome.baseline()
    base = _evaluation(genome, (("2026-08-13", "10", False),))
    delayed = _evaluation(genome, (("2026-08-13", "-2", False),))

    report = assess_execution_robustness((
        ScenarioEvaluation("base", base),
        ScenarioEvaluation("latency_2s", delayed),
    ))

    assert report.worst_net_eur == Decimal("-2")
    assert report.profitable_scenarios == 1
    assert report.scenario_count == 2
    assert report.robustness_eligible is False


def test_execution_robustness_keeps_unfilled_signals_in_participation():
    genome = StrategyGenome.baseline()
    rows = (
        ("2026-08-13", "5", False),
        ("2026-08-14", "0", True),
    )

    report = assess_execution_robustness((
        ScenarioEvaluation("base", _evaluation(genome, rows)),
        ScenarioEvaluation("stress", _evaluation(genome, rows)),
    ), minimum_participation=0.75)

    assert report.minimum_participation == 0.5
    assert report.robustness_eligible is False


def test_robust_ranking_prefers_worst_case_rule_quality_over_raw_lotage():
    stable = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.01,),
    )
    leveraged = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.10,),
    )
    stable_report = assess_execution_robustness((
        ScenarioEvaluation("base", _evaluation(stable, (("2026-08-13", "3", False),))),
        ScenarioEvaluation("stress", _evaluation(stable, (("2026-08-13", "2", False),))),
    ))
    leveraged_report = assess_execution_robustness((
        ScenarioEvaluation("base", _evaluation(leveraged, (("2026-08-13", "20", False),))),
        ScenarioEvaluation("stress", _evaluation(leveraged, (("2026-08-13", "10", False),))),
    ))

    ranked = rank_robust_candidates((leveraged_report, stable_report))

    assert ranked[0].genome == stable


def test_robust_ranking_treats_participation_as_gate_not_primary_prize():
    selective = StrategyGenome.baseline().with_change(time_exit_min=30)
    broad = StrategyGenome.baseline().with_change(time_exit_min=60)
    selective_rows = (
        ("2026-08-13", "10", False),
        ("2026-08-14", "0", True),
    )
    broad_rows = (
        ("2026-08-13", "1", False),
        ("2026-08-14", "1", False),
    )
    selective_report = assess_execution_robustness((
        ScenarioEvaluation(
            "base", _evaluation(selective, selective_rows)
        ),
        ScenarioEvaluation(
            "stress", _evaluation(selective, selective_rows)
        ),
    ), minimum_participation=0.5)
    broad_report = assess_execution_robustness((
        ScenarioEvaluation("base", _evaluation(broad, broad_rows)),
        ScenarioEvaluation("stress", _evaluation(broad, broad_rows)),
    ), minimum_participation=0.5)

    ranked = rank_robust_candidates((broad_report, selective_report))

    assert ranked[0].genome == selective


def test_robustness_requires_consistent_genome_and_unique_scenarios():
    first = StrategyGenome.baseline()
    second = first.with_change(time_exit_min=30)
    evaluation = _evaluation(first, (("2026-08-13", "1", False),))

    try:
        assess_execution_robustness((
            ScenarioEvaluation("same", evaluation),
            ScenarioEvaluation("same", replace(evaluation, genome=second)),
        ))
    except ValueError as exc:
        assert "scenario" in str(exc) or "genome" in str(exc)
    else:
        raise AssertionError("inconsistent robustness evidence was accepted")


def test_robustness_rejects_full_period_profit_that_fails_later_challenge():
    genome = StrategyGenome.baseline()
    values = (
        ("2026-08-12", "10", False),
        ("2026-08-13", "-1", False),
    )
    fold = ChronologicalFold(
        "late",
        "2026-08-12",
        "2026-08-12",
        "2026-08-13",
        "2026-08-13",
    )

    report = assess_execution_robustness((
        ScenarioEvaluation("base", _evaluation(genome, values)),
        ScenarioEvaluation("stress", _evaluation(genome, values)),
    ), folds=(fold,), minimum_positive_challenge_ratio=1.0)

    assert report.worst_net_eur == Decimal("9")
    assert report.positive_challenges == 0
    assert report.challenge_count == 2
    assert report.robustness_eligible is False


def test_robustness_can_apply_an_explicit_account_drawdown_budget():
    genome = StrategyGenome.baseline()
    evaluation = _evaluation(genome, (
        ("2026-08-12", "10", False),
        ("2026-08-13", "-5", False),
    ))

    report = assess_execution_robustness((
        ScenarioEvaluation("base", evaluation),
    ), maximum_drawdown_eur=Decimal("4"))

    assert report.maximum_drawdown_eur == Decimal("5")
    assert report.risk_eligible is False
    assert report.robustness_eligible is False


def test_robustness_materializes_fold_iterables_once():
    genome = StrategyGenome.baseline()
    values = (
        ("2026-08-12", "1", False),
        ("2026-08-13", "2", False),
    )
    fold = ChronologicalFold(
        "late",
        "2026-08-12",
        "2026-08-12",
        "2026-08-13",
        "2026-08-13",
    )

    report = assess_execution_robustness((
        ScenarioEvaluation("base", _evaluation(genome, values)),
    ), folds=(item for item in (fold,)))

    assert report.challenge_count == 1
    assert report.positive_challenges == 1
    assert report.positive_challenge_ratio == 1.0


def test_observational_equivalence_collapses_rules_with_identical_decisions():
    simple = StrategyGenome.baseline()
    filtered = simple.with_change(
        context_filter_mode="max_spread",
        context_filter_value=2.0,
    )
    rows = (
        ("2026-08-13", "5.00", False),
        ("2026-08-14", "-1.00", False),
    )
    simple_report = assess_execution_robustness((
        ScenarioEvaluation("base", _evaluation(simple, rows)),
        ScenarioEvaluation("stress", _evaluation(simple, rows)),
    ))
    filtered_report = assess_execution_robustness((
        ScenarioEvaluation("base", _evaluation(filtered, rows)),
        ScenarioEvaluation("stress", _evaluation(filtered, rows)),
    ))

    groups = group_observationally_equivalent((
        filtered_report,
        simple_report,
    ))

    assert len(groups) == 1
    assert groups[0].member_count == 2
    assert groups[0].representative.genome == simple
    assert groups[0].member_fingerprints == tuple(sorted((
        simple.fingerprint,
        filtered.fingerprint,
    )))


def test_observational_equivalence_keeps_one_cent_difference_separate():
    first = StrategyGenome.baseline()
    second = first.with_change(time_exit_min=60)
    first_report = assess_execution_robustness((
        ScenarioEvaluation(
            "base",
            _evaluation(first, (("2026-08-13", "5.00", False),)),
        ),
    ))
    second_report = assess_execution_robustness((
        ScenarioEvaluation(
            "base",
            _evaluation(second, (("2026-08-13", "5.01", False),)),
        ),
    ))

    groups = group_observationally_equivalent((first_report, second_report))

    assert len(groups) == 2
    assert all(group.member_count == 1 for group in groups)


def test_daily_stability_is_measured_across_every_execution_world():
    genome = StrategyGenome.baseline()
    base = _evaluation(genome, (
        ("2026-08-13", "2.00", False),
        ("2026-08-14", "3.00", False),
    ))
    stress = _evaluation(genome, (
        ("2026-08-13", "1.00", False),
        ("2026-08-14", "2.00", False),
    ))
    report = assess_execution_robustness((
        ScenarioEvaluation("base", base),
        ScenarioEvaluation("stress", stress),
    ))

    stability = assess_robust_daily_stability(report, samples=1_000, seed=7)

    assert stability.evidence_complete is True
    assert stability.scenario_count == 2
    assert stability.minimum_bootstrap_probability_positive == 1.0
    assert stability.worst_bootstrap_p05_eur == Decimal("2.00")
    assert stability.worst_normalized_bootstrap_p05_per_001 == Decimal("0.5000")


def test_group_ranking_prefers_repeatable_days_over_one_lucky_day():
    stable = StrategyGenome.baseline().with_change(time_exit_min=60)
    lucky = StrategyGenome.baseline().with_change(time_exit_min=90)
    stable_report = assess_execution_robustness((
        ScenarioEvaluation("base", _evaluation(stable, (
            ("2026-08-13", "2.00", False),
            ("2026-08-14", "2.00", False),
        ))),
    ))
    lucky_report = assess_execution_robustness((
        ScenarioEvaluation("base", _evaluation(lucky, (
            ("2026-08-13", "12.00", False),
            ("2026-08-14", "-6.00", False),
        ))),
    ))
    groups = group_observationally_equivalent((lucky_report, stable_report))
    stability = {
        group.representative.genome.fingerprint: assess_robust_daily_stability(
            group.representative,
            samples=10_000,
            seed=91,
        )
        for group in groups
    }

    ranked = rank_observational_groups(groups, stability)

    assert ranked[0].representative.genome == stable


def test_capital_search_frontier_reports_distinct_behavior_not_raw_variants():
    from tools.run_dubai_capital_search import _build_behavior_frontier

    simple = StrategyGenome.baseline()
    filtered = simple.with_change(
        context_filter_mode="max_spread",
        context_filter_value=2.0,
    )
    rows = (
        ("2026-08-13", "2.00", False),
        ("2026-08-14", "3.00", False),
    )
    reports = tuple(
        assess_execution_robustness((
            ScenarioEvaluation("base", _evaluation(genome, rows)),
        ))
        for genome in (simple, filtered)
    )

    frontier = _build_behavior_frontier(reports, samples=1_000, seed=5)

    assert len(frontier) == 1
    group, stability = frontier[0]
    assert group.member_count == 2
    assert group.representative.genome == simple
    assert stability.evidence_complete is True


def test_primary_research_gate_does_not_discard_a_rule_for_raw_lotage_risk():
    from tools.run_dubai_capital_search import _primary_allows

    genome = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(1.0,),
    )
    evaluation = _evaluation(genome, (
        ("2026-07-27", "500.00", False),
        ("2026-07-28", "-200.00", False),
        ("2026-08-04", "1.00", False),
        ("2026-08-06", "1.00", False),
        ("2026-08-10", "1.00", False),
        ("2026-08-13", "1.00", False),
    ))

    assert evaluation.max_drawdown_eur == Decimal("200.00")
    assert _primary_allows(
        evaluation,
        minimum_challenge_ratio=1.0,
    ) is True
