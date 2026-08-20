from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from research.dubai_iterative.contracts import SearchSpace, StrategyGenome
from research.dubai_iterative.engine import EntryRecord, ExitRecord, SimulationResult
from research.dubai_iterative.evolution import (
    CandidateEvaluation,
    Diagnosis,
    crossover,
    deduplicate,
    diagnose,
    diagnose_against_reference,
    evolve_generation,
    mutate_from_diagnosis,
    pareto_front,
    sample_diverse_population,
    seed_population,
)


def _result(
    signal_id="canal1_1",
    *,
    day="2026-07-27",
    pnl="0.00",
    max_favourable="0.00",
    max_adverse="0.00",
    floating_drawdown="0.00",
    exit_reason="provider_tp",
    filled_volume=0.04,
    unfilled=False,
    blockers=(),
):
    result = SimulationResult(
        signal_id=signal_id,
        strategy_fingerprint="test",
        confidence_layer="observed_entry_management",
        entries=(),
        exits=(),
        pnl_eur=None if blockers else Decimal(pnl),
        exit_reason=exit_reason,
        max_favourable_eur=Decimal(max_favourable),
        max_adverse_eur=Decimal(max_adverse),
        max_floating_drawdown_eur=Decimal(floating_drawdown),
        max_favourable_move=0.0,
        max_adverse_move=0.0,
        blockers=tuple(blockers),
        last_tick_index=1,
        unfilled=unfilled,
        filled_volume=filled_volume,
    )
    return day, result


def _evaluation(genome=None, *rows):
    return CandidateEvaluation.from_results(
        genome or StrategyGenome.baseline(),
        rows or (_result(),),
    )


def test_candidate_evaluation_reports_real_participation_in_signals():
    evaluation = _evaluation(
        StrategyGenome.baseline(),
        _result("filled_1", pnl="2.00"),
        _result("skipped", pnl="0.00", exit_reason="not_filled", unfilled=True, filled_volume=0.0),
        _result("filled_2", pnl="-1.00"),
    )

    assert evaluation.total_signal_count == 3
    assert evaluation.filled_signal_count == 2
    assert evaluation.participation_rate == 2 / 3


def test_giveback_diagnosis_generates_profit_protection_children():
    evaluation = _evaluation(
        StrategyGenome.baseline(),
        _result(pnl="-12.00", max_favourable="18.00", max_adverse="-14.00"),
    )

    diagnosis = diagnose(evaluation)
    children = mutate_from_diagnosis(
        evaluation.genome,
        diagnosis,
        search_space=SearchSpace(),
        seed=7,
    )

    assert "profit_given_back" in diagnosis.labels
    assert any(child.profit_lock_arm is not None for child in children)
    assert all(child.parent_fingerprints == (evaluation.genome.fingerprint,) for child in children)


def test_fingerprints_prevent_duplicate_children():
    baseline = StrategyGenome.baseline()
    duplicate_with_different_story = baseline.with_lineage(
        parent_fingerprints=("parent",),
        mutation_reason="same_rules",
        lineage_depth=3,
    )

    population = deduplicate([baseline, duplicate_with_different_story])

    assert len(population) == 1


class RecordingCritic:
    def __init__(self):
        self.seen_signal_ids = []

    def diagnose(self, evaluation):
        self.seen_signal_ids.extend(
            result.signal_id for _day, result in evaluation.results
        )
        return Diagnosis(labels=("profit_given_back",), evidence=())


def test_challenge_metrics_are_not_available_to_mutation():
    critic = RecordingCritic()
    training = _evaluation(
        StrategyGenome.baseline(),
        _result("train_1", pnl="-2.00", max_favourable="4.00"),
    )
    challenge = _evaluation(
        StrategyGenome.baseline(),
        _result("poison_challenge", pnl="9999.00", max_favourable="9999.00"),
    )

    evolve_generation(
        [training],
        critic=critic,
        challenge_results=[challenge],
        search_space=SearchSpace(),
        seed=11,
    )

    assert critic.seen_signal_ids == ["train_1"]


def test_seed_population_explores_both_sides_of_observed_exposure():
    population = seed_population(
        SearchSpace(min_total_volume=0.01, max_total_volume=0.20, max_legs=8),
        seed=20260817,
    )
    totals = [sum(genome.volume_weights) for genome in population]

    assert min(totals) < 0.04
    assert max(totals) > 0.04
    assert {genome.entry_mode for genome in population} >= {
        "actual_mt5",
        "delay",
        "pullback",
        "momentum",
    }
    assert {genome.target_mode for genome in population} >= {
        "provider_per_leg",
        "fixed_basket",
        "partial_runner",
    }
    assert all(not genome.validation_errors() for genome in population)
    assert all(not SearchSpace().validation_errors(genome) for genome in population)


def test_loss_diagnosis_can_reduce_or_increase_exposure_within_run_space():
    genome = StrategyGenome.baseline()
    losing = _evaluation(
        genome,
        _result(pnl="-25.00", max_favourable="2.00", max_adverse="-27.00"),
    )
    winning = _evaluation(
        genome,
        _result(pnl="12.00", max_favourable="13.00", max_adverse="-1.00"),
        _result("canal1_2", day="2026-07-28", pnl="10.00", max_favourable="11.00"),
    )

    loss_children = mutate_from_diagnosis(
        genome,
        diagnose(losing),
        search_space=SearchSpace(max_total_volume=0.20),
        seed=3,
    )
    win_children = mutate_from_diagnosis(
        genome,
        diagnose(winning),
        search_space=SearchSpace(max_total_volume=0.20),
        seed=3,
    )

    assert any(sum(child.volume_weights) < 0.04 for child in loss_children)
    assert any(sum(child.volume_weights) > 0.04 for child in win_children)


def test_pareto_front_keeps_real_profit_drawdown_tradeoff():
    low_risk = _evaluation(
        StrategyGenome.baseline(),
        _result(pnl="10.00", max_favourable="10.00", floating_drawdown="3.00"),
    )
    high_return = _evaluation(
        StrategyGenome.baseline().with_change(be_mode="none"),
        _result(pnl="15.00", max_favourable="15.00", floating_drawdown="8.00"),
    )
    dominated = _evaluation(
        StrategyGenome.baseline().with_change(be_mode="price", be_trigger=2.0),
        _result(pnl="5.00", max_favourable="5.00", floating_drawdown="9.00"),
    )

    frontier = pareto_front([low_risk, high_return, dominated])

    assert {item.genome.fingerprint for item in frontier} == {
        low_risk.genome.fingerprint,
        high_return.genome.fingerprint,
    }


def test_pareto_does_not_call_uniformly_larger_lotage_a_better_rule():
    small = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.01,),
    )
    large = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.10,),
    )
    small_evaluation = _evaluation(
        small,
        _result(
            pnl="2.00",
            max_favourable="3.00",
            max_adverse="-1.00",
            floating_drawdown="1.00",
            filled_volume=0.01,
        ),
    )
    large_evaluation = _evaluation(
        large,
        _result(
            pnl="20.00",
            max_favourable="30.00",
            max_adverse="-10.00",
            floating_drawdown="10.00",
            filled_volume=0.10,
        ),
    )

    frontier = pareto_front((small_evaluation, large_evaluation))

    assert small_evaluation.normalized_net_per_001 == Decimal("2.00")
    assert large_evaluation.normalized_net_per_001 == Decimal("2.00")
    assert small_evaluation.normalized_max_drawdown_per_001 == Decimal("1.00")
    assert large_evaluation.normalized_max_drawdown_per_001 == Decimal("1.00")
    assert [item.genome.fingerprint for item in frontier] == [small.fingerprint]


def test_pareto_treats_zero_drawdown_as_real_zero_not_missing_data():
    zero_drawdown = _evaluation(
        StrategyGenome.baseline().with_change(time_exit_min=30),
        _result(
            pnl="5.00",
            max_favourable="5.00",
            floating_drawdown="0.00",
        ),
    )
    positive_drawdown = _evaluation(
        StrategyGenome.baseline().with_change(time_exit_min=60),
        _result(
            pnl="5.00",
            max_favourable="5.00",
            floating_drawdown="1.00",
        ),
    )

    frontier = pareto_front((zero_drawdown, positive_drawdown))

    assert [item.genome.fingerprint for item in frontier] == [
        zero_drawdown.genome.fingerprint
    ]


def test_normalized_result_penalizes_signals_that_were_not_filled():
    genome = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.02,),
    )
    evaluation = _evaluation(
        genome,
        _result("filled", pnl="4.00", filled_volume=0.02),
        _result("skipped", day="2026-07-28", pnl="0.00", filled_volume=0.0),
    )

    assert evaluation.normalized_net_per_001 == Decimal("2.00")


def test_crossover_copies_whole_compatible_blocks_and_records_both_parents():
    left = StrategyGenome.baseline().with_change(
        entry_mode="pullback",
        entry_value=1.0,
        entry_expiry_min=10,
        leg_count=2,
        volume_weights=(0.01, 0.01),
        target_mode="fixed_basket",
        target_value=8.0,
        be_mode="none",
        time_exit_min=180,
    )
    right = StrategyGenome.baseline().with_change(
        entry_mode="momentum",
        entry_value=2.0,
        entry_expiry_min=30,
        leg_count=6,
        volume_weights=(0.02,) * 6,
        target_mode="partial_runner",
        target_value=5.0,
        partial_fraction=0.5,
        runner_target=15.0,
        stop_mode="basket_money",
        stop_value=12.0,
        time_exit_min=180,
    )

    children = crossover(
        left,
        right,
        search_space=SearchSpace(max_total_volume=0.20, max_legs=8),
        seed=9,
    )

    assert children
    for child in children:
        assert child.parent_fingerprints == (left.fingerprint, right.fingerprint)
        assert not child.validation_errors()
        assert not SearchSpace(max_total_volume=0.20, max_legs=8).validation_errors(child)
        assert (child.entry_mode, child.entry_value, child.entry_expiry_min) in {
            (left.entry_mode, left.entry_value, left.entry_expiry_min),
            (right.entry_mode, right.entry_value, right.entry_expiry_min),
        }
        assert (child.leg_count, child.volume_weights) in {
            (left.leg_count, left.volume_weights),
            (right.leg_count, right.volume_weights),
        }


def test_diagnosis_finds_a_last_leg_that_reduces_the_basket_result():
    now = datetime(2026, 8, 17, 9, tzinfo=timezone.utc)
    result = SimulationResult(
        signal_id="canal1_legs",
        strategy_fingerprint="test",
        confidence_layer="observed_entry_management",
        entries=(
            EntryRecord("first", 0, now, 4300.0, 0.01, "actual_mt5"),
            EntryRecord("last", 0, now, 4300.0, 0.01, "actual_mt5"),
        ),
        exits=(
            ExitRecord("first", 1, now, 4300.0, 4308.0, 0.01, Decimal("8.00"), "provider_tp"),
            ExitRecord("last", 1, now, 4300.0, 4295.0, 0.01, Decimal("-5.00"), "provider_sl"),
        ),
        pnl_eur=Decimal("3.00"),
        exit_reason="provider_sl",
        max_favourable_eur=Decimal("8.00"),
        max_adverse_eur=Decimal("-5.00"),
        max_floating_drawdown_eur=Decimal("5.00"),
        max_favourable_move=8.0,
        max_adverse_move=-5.0,
        blockers=(),
        last_tick_index=1,
        unfilled=False,
        filled_volume=0.02,
    )

    found = diagnose(CandidateEvaluation.from_results(
        StrategyGenome.baseline().with_change(
            leg_count=2,
            volume_weights=(0.01, 0.01),
        ),
        (("2026-08-17", result),),
    ))

    assert "marginal_leg_damage" in found.labels


def test_reference_comparison_finds_stop_recovery_and_premature_target():
    stopped = _evaluation(
        StrategyGenome.baseline().with_change(
            stop_mode="basket_money",
            stop_value=10.0,
            target_mode="fixed_basket",
            target_value=5.0,
        ),
        _result(pnl="-10.00", max_favourable="1.00", exit_reason="basket_stop"),
    )
    recovered = _evaluation(
        StrategyGenome.baseline().with_change(stop_mode="none"),
        _result(pnl="12.00", max_favourable="14.00", exit_reason="provider_tp"),
    )
    early = _evaluation(
        StrategyGenome.baseline().with_change(
            target_mode="fixed_basket",
            target_value=3.0,
        ),
        _result(pnl="3.00", max_favourable="3.00", exit_reason="basket_target"),
    )

    stop_diagnosis = diagnose_against_reference(stopped, recovered)
    target_diagnosis = diagnose_against_reference(early, recovered)

    assert "stop_before_recovery" in stop_diagnosis.labels
    assert "premature_target" in target_diagnosis.labels


def test_seed_population_exposes_every_supported_strategy_family():
    population = seed_population(SearchSpace(max_total_volume=0.20), seed=21)

    assert {item.target_mode for item in population} == {
        "provider_per_leg",
        "provider_target_all",
        "fixed_basket",
        "fixed_move",
        "partial_runner",
        "none",
    }
    assert {item.be_mode for item in population} == {
        "provider",
        "none",
        "price",
        "delayed",
        "partial",
    }
    assert {item.stop_mode for item in population} == {
        "provider",
        "fixed_move",
        "basket_money",
        "none",
    }
    assert {item.context_filter_mode for item in population} == {
        "none",
        "max_spread",
        "time_window",
        "max_volatility",
        "min_reward_risk",
    }
    assert {item.entry_ladder_mode for item in population} == {
        "simultaneous",
        "adverse",
        "favourable",
    }


def test_seed_population_reaches_the_explicit_one_lot_research_boundary():
    population = seed_population(SearchSpace(max_total_volume=1.0), seed=21)
    totals = {round(sum(item.volume_weights), 10) for item in population}

    assert min(totals) == 0.01
    assert 0.04 in totals
    assert max(totals) == 1.0
    assert any(
        round(sum(item.volume_weights), 10) == 1.0 and item.leg_count > 1
        for item in population
    )


def test_diverse_scouts_are_deterministic_valid_and_radically_combined():
    space = SearchSpace(max_total_volume=0.50, max_legs=12)

    first = sample_diverse_population(space, seed=44, count=256)
    second = sample_diverse_population(space, seed=44, count=256)

    assert [item.fingerprint for item in first] == [
        item.fingerprint for item in second
    ]
    assert len(first) == 256
    assert all(not item.validation_errors() for item in first)
    assert all(not space.validation_errors(item) for item in first)
    assert min(sum(item.volume_weights) for item in first) < 0.04
    assert max(sum(item.volume_weights) for item in first) > 0.20
    assert any(
        item.entry_mode != "actual_mt5"
        and item.target_mode not in {"provider_per_leg", "provider_target_all"}
        and item.be_mode != "provider"
        and item.stop_mode != "provider"
        and item.provider_management_mode != "exact"
        for item in first
    )
    assert max(item.entry_expiry_min for item in first) <= space.max_entry_expiry_min
    assert max(item.time_exit_min for item in first) <= space.max_time_exit_min


def test_diverse_partial_scouts_only_use_broker_executable_volumes():
    space = SearchSpace(max_total_volume=0.50, max_legs=12)
    population = sample_diverse_population(space, seed=91, count=512)
    partials = [item for item in population if item.target_mode == "partial_runner"]

    assert partials
    assert all(
        "unexecutable_partial_volume" not in space.validation_errors(item)
        for item in partials
    )
