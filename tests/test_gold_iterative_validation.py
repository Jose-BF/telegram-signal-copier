from dataclasses import replace
from decimal import Decimal

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import SimulationResult
from research.dubai_iterative.evolution import CandidateEvaluation
from research.dubai_iterative.robustness import (
    ScenarioEvaluation,
    assess_execution_robustness,
)
from research.gold_iterative.validation import (
    GoldStabilityPolicy,
    validate_gold_candidates,
)


def _result(signal_id: str, pnl: str) -> SimulationResult:
    value = Decimal(pnl)
    return SimulationResult(
        signal_id=signal_id,
        strategy_fingerprint="fixture",
        confidence_layer="counterfactual_entry",
        entries=(),
        exits=(),
        pnl_eur=value,
        exit_reason="fixture",
        max_favourable_eur=max(value, Decimal("0")),
        max_adverse_eur=min(value, Decimal("0")),
        max_floating_drawdown_eur=max(-value, Decimal("0")),
        max_favourable_move=0.0,
        max_adverse_move=0.0,
        blockers=(),
        last_tick_index=0,
        unfilled=False,
        filled_volume=0.01,
    )


def _evaluation(genome, values) -> CandidateEvaluation:
    return CandidateEvaluation.from_results(
        genome,
        (
            (day, _result(f"{genome.fingerprint[:8]}_{index}", pnl))
            for index, (day, pnl) in enumerate(values)
        ),
    )


def _assessment(genome, values):
    evaluation = _evaluation(genome, values)
    return assess_execution_robustness((
        ScenarioEvaluation("base", evaluation),
        ScenarioEvaluation("stress", evaluation),
    ))


def test_gold_validation_keeps_repeatable_candidate_and_rejects_lucky_total():
    stable = StrategyGenome.baseline().with_change(time_exit_min=30)
    lucky = StrategyGenome.baseline().with_change(time_exit_min=60)
    stable_values = (
        ("2026-08-10", "2.00"),
        ("2026-08-11", "2.00"),
        ("2026-08-12", "2.00"),
    )
    lucky_values = (
        ("2026-08-10", "20.00"),
        ("2026-08-11", "-6.00"),
        ("2026-08-12", "-6.00"),
    )

    selection = validate_gold_candidates(
        (
            _assessment(lucky, lucky_values),
            _assessment(stable, stable_values),
        ),
        policy=GoldStabilityPolicy(bootstrap_samples=1_000, seed=7),
    )

    assert [item.genome.fingerprint for item in selection.eligible] == [
        stable.fingerprint
    ]
    rejected = {
        item.genome.fingerprint: item for item in selection.rejected
    }
    assert "bootstrap_probability_below_threshold" in rejected[
        lucky.fingerprint
    ].blockers
    assert "bootstrap_p05_not_positive" in rejected[lucky.fingerprint].blockers
    assert "leave_one_day_out_not_robust" in rejected[lucky.fingerprint].blockers


def test_gold_validation_never_resurrects_execution_robustness_rejection():
    genome = StrategyGenome.baseline().with_change(time_exit_min=30)
    positive = _evaluation(genome, (
        ("2026-08-10", "2.00"),
        ("2026-08-11", "2.00"),
    ))
    negative = _evaluation(genome, (
        ("2026-08-10", "-1.00"),
        ("2026-08-11", "-1.00"),
    ))
    assessment = assess_execution_robustness((
        ScenarioEvaluation("base", positive),
        ScenarioEvaluation("stress", negative),
    ))

    selection = validate_gold_candidates(
        (assessment,),
        policy=GoldStabilityPolicy(bootstrap_samples=1_000, seed=9),
    )

    assert selection.eligible == ()
    assert selection.rejected[0].blockers[0] == (
        "execution_robustness_failed"
    )


def test_gold_validation_bootstraps_only_post_discovery_days():
    genome = StrategyGenome.baseline().with_change(time_exit_min=30)
    full_history = _evaluation(genome, (
        ("2026-08-10", "100.00"),
        ("2026-08-11", "-1.00"),
        ("2026-08-12", "-1.00"),
    ))
    assessment = assess_execution_robustness((
        ScenarioEvaluation("base", full_history),
        ScenarioEvaluation("stress", full_history),
    ))
    assessment = replace(
        assessment,
        discovery_fold_name="fold_02",
        validation_fold_names=("fold_02", "fold_03"),
        validation_days=("2026-08-11", "2026-08-12"),
        validation_signal_count=2,
    )

    selection = validate_gold_candidates(
        (assessment,),
        policy=GoldStabilityPolicy(bootstrap_samples=1_000, seed=11),
    )

    assert selection.eligible == ()
    stability = selection.rejected[0].stability
    assert stability.scenarios[0][1].day_totals == (
        ("2026-08-11", Decimal("-1.00")),
        ("2026-08-12", Decimal("-1.00")),
    )
    assert "bootstrap_probability_below_threshold" in (
        selection.rejected[0].blockers
    )


def test_gold_stability_policy_rejects_invalid_probability_threshold():
    try:
        GoldStabilityPolicy(minimum_bootstrap_probability_positive=1.01)
    except ValueError as exc:
        assert "probability" in str(exc)
    else:
        raise AssertionError("invalid stability threshold was accepted")
