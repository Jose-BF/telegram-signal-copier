from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

import numpy as np
import pytest

from research.dubai_iterative.contracts import SearchBudget, SearchSpace, StrategyGenome
from research.dubai_iterative.engine import SimulationResult
from research.dubai_iterative.evolution import CandidateEvaluation, Diagnosis
from research.dubai_iterative.search import (
    ChronologicalFold,
    ChronologicalSearchReport,
    DEFAULT_DUBAI_FOLDS,
    SearchReport,
    SearchCheckpointError,
    classify_retrospective,
    cross_validate_frontier_candidates,
    run_chronological_search,
    run_search,
    _next_population,
    _replace_checkpoint,
)


@dataclass(frozen=True)
class TinyPath:
    signal_id: str
    day: str


@dataclass(frozen=True)
class TinyDataset:
    paths: tuple[TinyPath, ...]
    source_hashes: dict[str, str]


def _dataset():
    return TinyDataset(
        paths=(
            TinyPath("train_1", "2026-07-27"),
            TinyPath("train_2", "2026-07-28"),
            TinyPath("challenge_1", "2026-07-29"),
        ),
        source_hashes={"fixture": "abc123"},
    )


def _fold():
    return ChronologicalFold(
        name="tiny",
        development_from="2026-07-27",
        development_to="2026-07-28",
        challenge_from="2026-07-29",
        challenge_to="2026-07-29",
    )


def _flat_evaluator(path, genome):
    return SimulationResult(
        signal_id=path.signal_id,
        strategy_fingerprint=genome.fingerprint,
        confidence_layer="fixture",
        entries=(),
        exits=(),
        pnl_eur=Decimal("1.00"),
        exit_reason="provider_tp",
        max_favourable_eur=Decimal("1.00"),
        max_adverse_eur=Decimal("0.00"),
        max_floating_drawdown_eur=Decimal("0.00"),
        max_favourable_move=1.0,
        max_adverse_move=0.0,
        blockers=(),
        last_tick_index=1,
        unfilled=False,
        filled_volume=sum(genome.volume_weights),
    )


class StepClock:
    def __init__(self, step):
        self.value = 0.0
        self.step = step

    def __call__(self):
        current = self.value
        self.value += self.step
        return current


@pytest.mark.parametrize(
    ("budget", "clock", "expected"),
    (
        (SearchBudget(max_generations=2), StepClock(0.0), "max_generations"),
        (SearchBudget(max_evaluations=5), StepClock(0.0), "max_evaluations"),
        (SearchBudget(max_wall_seconds=1), StepClock(2.0), "max_wall_seconds"),
        (SearchBudget(patience_generations=1), StepClock(0.0), "no_improvement"),
        (SearchBudget(max_lineage_depth=1), StepClock(0.0), "max_lineage_depth"),
    ),
)
def test_search_always_stops_at_configured_boundary(tmp_path, budget, clock, expected):
    report = run_search(
        _dataset(),
        fold=_fold(),
        budget=budget,
        search_space=SearchSpace(),
        output_dir=tmp_path / expected,
        evaluator=_flat_evaluator,
        clock=clock,
        population_size=8,
    )

    assert report.stop_reason == expected
    assert report.generations_completed <= budget.max_generations
    assert report.evaluations <= budget.max_evaluations


def test_imported_parent_is_evaluated_in_the_first_generation(tmp_path):
    parent = StrategyGenome.baseline().with_change(
        entry_mode="pullback",
        entry_value=1.0,
        entry_expiry_min=30,
        entry_ladder_mode="adverse",
        entry_ladder_step=0.25,
        leg_count=2,
        volume_weights=(0.02, 0.01),
        target_mode="fixed_move",
        target_value=0.5,
        be_mode="none",
        stop_mode="fixed_move",
        stop_value=8.0,
        provider_management_mode="close_only",
        time_exit_min=90,
    )
    first_generation = []

    run_search(
        _dataset(),
        fold=_fold(),
        budget=SearchBudget(max_generations=1, patience_generations=10),
        search_space=SearchSpace(),
        output_dir=tmp_path / "parents",
        evaluator=_flat_evaluator,
        population_size=8,
        initial_genomes=(parent,),
        evaluation_callback=lambda _fold, generation, rows: (
            first_generation.extend(item.genome.fingerprint for item in rows)
            if generation == 1
            else None
        ),
    )

    assert parent.fingerprint in first_generation


def test_imported_parent_must_fit_the_explicit_search_envelope(tmp_path):
    parent = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(1.01,),
    )

    with pytest.raises(ValueError, match="outside_search_volume"):
        run_search(
            _dataset(),
            fold=_fold(),
            budget=SearchBudget(max_generations=1),
            search_space=SearchSpace(max_total_volume=1.0),
            output_dir=tmp_path / "invalid-parent",
            evaluator=_flat_evaluator,
            initial_genomes=(parent,),
        )


def test_resume_produces_same_frontier_as_uninterrupted_run(tmp_path):
    common = dict(
        dataset=_dataset(),
        fold=_fold(),
        search_space=SearchSpace(),
        evaluator=_flat_evaluator,
        seed=20260817,
        population_size=8,
    )
    uninterrupted = run_search(
        **common,
        budget=SearchBudget(max_generations=4, patience_generations=10),
        output_dir=tmp_path / "uninterrupted",
    )
    first_half = run_search(
        **common,
        budget=SearchBudget(max_generations=2, patience_generations=10),
        output_dir=tmp_path / "resumed",
    )
    resumed = run_search(
        **common,
        budget=SearchBudget(max_generations=4, patience_generations=10),
        output_dir=tmp_path / "resumed",
        resume_from=first_half.checkpoint_path,
    )

    assert resumed.frontier_fingerprints == uninterrupted.frontier_fingerprints
    assert resumed.evaluations == uninterrupted.evaluations
    assert resumed.generations_completed == 4


def test_parallel_evaluation_is_identical_to_serial_evaluation(tmp_path):
    common = dict(
        dataset=_dataset(),
        fold=_fold(),
        budget=SearchBudget(max_generations=3, patience_generations=10),
        search_space=SearchSpace(),
        evaluator=_flat_evaluator,
        seed=20260817,
        population_size=12,
    )

    serial = run_search(
        **common,
        output_dir=tmp_path / "serial",
        workers=1,
    )
    parallel = run_search(
        **common,
        output_dir=tmp_path / "parallel",
        workers=4,
    )

    assert parallel.frontier_fingerprints == serial.frontier_fingerprints
    assert parallel.evaluations == serial.evaluations
    assert parallel.generations_completed == serial.generations_completed


class RecordingCritic:
    def __init__(self):
        self.seen = []

    def diagnose(self, evaluation):
        self.seen.extend(result.signal_id for _day, result in evaluation.results)
        return Diagnosis(("no_dominant_failure",), ())


def test_challenge_rows_are_frozen_until_search_has_finished(tmp_path):
    critic = RecordingCritic()

    report = run_search(
        _dataset(),
        fold=_fold(),
        budget=SearchBudget(max_generations=2, patience_generations=10),
        search_space=SearchSpace(),
        output_dir=tmp_path,
        evaluator=_flat_evaluator,
        critic=critic,
        population_size=4,
    )

    assert critic.seen
    assert set(critic.seen) == {"train_1", "train_2"}
    assert report.challenge_evaluations
    assert {
        result.signal_id
        for evaluation in report.challenge_evaluations
        for _day, result in evaluation.results
    } == {"challenge_1"}


def test_resume_rejects_a_different_evidence_universe(tmp_path):
    first = run_search(
        _dataset(),
        fold=_fold(),
        budget=SearchBudget(max_generations=1),
        search_space=SearchSpace(),
        output_dir=tmp_path,
        evaluator=_flat_evaluator,
        population_size=4,
    )
    changed = TinyDataset(_dataset().paths, {"fixture": "changed"})

    with pytest.raises(SearchCheckpointError, match="source hashes"):
        run_search(
            changed,
            fold=_fold(),
            budget=SearchBudget(max_generations=2),
            search_space=SearchSpace(),
            output_dir=tmp_path,
            evaluator=_flat_evaluator,
            resume_from=first.checkpoint_path,
            population_size=4,
        )


def test_resume_rejects_different_execution_assumptions(tmp_path):
    first = run_search(
        _dataset(),
        fold=_fold(),
        budget=SearchBudget(max_generations=1),
        search_space=SearchSpace(),
        output_dir=tmp_path,
        evaluator=_flat_evaluator,
        population_size=4,
        experiment_context={"execution": {"latency_ms": 0}},
    )

    with pytest.raises(SearchCheckpointError, match="experiment context"):
        run_search(
            _dataset(),
            fold=_fold(),
            budget=SearchBudget(max_generations=2),
            search_space=SearchSpace(),
            output_dir=tmp_path,
            evaluator=_flat_evaluator,
            resume_from=first.checkpoint_path,
            population_size=4,
            experiment_context={"execution": {"latency_ms": 500}},
        )


def test_known_historical_overfit_is_never_promoted():
    assessment = classify_retrospective(
        train_net=Decimal("100.33"),
        challenge_net=Decimal("-83.57"),
    )

    assert assessment.confidence == "retrospective_unstable"
    assert assessment.promotion_eligible is False


def test_complete_coordinator_runs_the_four_expanding_contract_folds(tmp_path):
    dataset = TinyDataset(
        paths=(
            TinyPath("july", "2026-07-27"),
            TinyPath("fold_1_challenge", "2026-08-04"),
            TinyPath("fold_2_challenge", "2026-08-06"),
            TinyPath("fold_3_challenge", "2026-08-10"),
            TinyPath("fold_4_challenge", "2026-08-13"),
        ),
        source_hashes={"fixture": "four-folds"},
    )

    report = run_chronological_search(
        dataset,
        folds=DEFAULT_DUBAI_FOLDS,
        budget=SearchBudget(max_generations=1),
        search_space=SearchSpace(),
        output_dir=tmp_path,
        evaluator=_flat_evaluator,
        population_size=2,
    )

    assert [item.fold.name for item in report.fold_reports] == [
        "fold_1",
        "fold_2",
        "fold_3",
        "fold_4",
    ]
    for fold_report in report.fold_reports:
        assert fold_report.challenge_evaluations
        assert all(
            fold_report.fold.challenge_contains(day)
            for evaluation in fold_report.challenge_evaluations
            for day, _result in evaluation.results
        )


def test_cross_fold_gate_retests_every_frontier_candidate_on_all_challenges(
    tmp_path,
):
    fragile = StrategyGenome.baseline().with_change(time_exit_min=15)
    robust = StrategyGenome.baseline().with_change(time_exit_min=30)
    folds = (
        ChronologicalFold(
            "first", "2026-07-27", "2026-07-27",
            "2026-07-28", "2026-07-28",
        ),
        ChronologicalFold(
            "second", "2026-07-27", "2026-07-28",
            "2026-07-29", "2026-07-29",
        ),
    )
    dataset = TinyDataset(
        paths=(
            TinyPath("day_1", "2026-07-27"),
            TinyPath("day_2", "2026-07-28"),
            TinyPath("day_3", "2026-07-29"),
        ),
        source_hashes={"fixture": "cross-fold"},
    )

    def evaluator(path, genome):
        if genome.fingerprint == fragile.fingerprint:
            pnl = {
                "2026-07-27": "10.00",
                "2026-07-28": "2.00",
                "2026-07-29": "-20.00",
            }[path.day]
        else:
            pnl = "1.00"
        return replace(_flat_evaluator(path, genome), pnl_eur=Decimal(pnl))

    def development(genome, fold):
        return CandidateEvaluation.from_results(
            genome,
            (
                (path.day, evaluator(path, genome))
                for path in dataset.paths
                if fold.development_contains(path.day)
            ),
        )

    reports = tuple(
        SearchReport(
            fold=fold,
            stop_reason="max_generations",
            generations_completed=1,
            evaluations=1,
            elapsed_seconds=1.0,
            frontier=(development(genome, fold),),
            challenge_evaluations=(),
            checkpoint_path=tmp_path / fold.name / "checkpoint.json",
            stale_generations=0,
            generation_summaries=(),
        )
        for fold, genome in zip(folds, (fragile, robust), strict=True)
    )

    progress = []
    validated = cross_validate_frontier_candidates(
        dataset,
        ChronologicalSearchReport(reports),
        evaluator=evaluator,
        workers=2,
        progress_callback=lambda completed, total: progress.append(
            (completed, total)
        ),
    )

    assert validated.considered_count == 2
    assert [
        item.genome.fingerprint for item in validated.eligible
    ] == [robust.fingerprint]
    rejected = {
        item.genome.fingerprint: item for item in validated.rejected
    }
    assert rejected[fragile.fingerprint].worst_net_eur == Decimal("-8.00")
    assert rejected[fragile.fingerprint].positive_challenges == 1
    assert rejected[fragile.fingerprint].challenge_count == 2
    assert progress[-1] == (2, 2)


def test_generation_zero_reserves_space_for_distant_strategy_scouts(tmp_path):
    evaluated = []

    run_search(
        _dataset(),
        fold=_fold(),
        budget=SearchBudget(max_generations=1),
        search_space=SearchSpace(max_total_volume=0.50),
        output_dir=tmp_path,
        evaluator=_flat_evaluator,
        population_size=48,
        evaluation_callback=lambda _fold, _generation, rows: evaluated.extend(rows),
    )

    assert len(evaluated) == 48
    assert any(
        item.genome.entry_mode != "actual_mt5"
        and item.genome.target_mode != "provider_per_leg"
        and item.genome.be_mode != "provider"
        and item.genome.stop_mode != "provider"
        for item in evaluated
    )


def test_generation_zero_always_contains_the_observed_baseline(tmp_path):
    evaluated = []

    run_search(
        _dataset(),
        fold=_fold(),
        budget=SearchBudget(max_generations=1),
        search_space=SearchSpace(max_total_volume=0.50),
        output_dir=tmp_path,
        evaluator=_flat_evaluator,
        population_size=12,
        evaluation_callback=lambda _fold, _generation, rows: evaluated.extend(rows),
    )

    assert StrategyGenome.baseline().fingerprint in {
        item.genome.fingerprint for item in evaluated
    }


def test_next_generation_systematically_explores_exposure_above_and_below_parent():
    parent = StrategyGenome.baseline()
    evaluation = CandidateEvaluation.from_results(
        parent,
        ((path.day, _flat_evaluator(path, parent)) for path in _dataset().paths[:2]),
    )

    children = _next_population(
        archive=(evaluation,),
        current=(evaluation,),
        critic=None,
        search_space=SearchSpace(max_total_volume=1.00, max_legs=12),
        seeds=(),
        seen={parent.fingerprint},
        rng=np.random.default_rng(71),
        seed=71,
        population_size=256,
        max_lineage_depth=12,
    )

    exposure_children = [
        child for child in children
        if child.mutation_reason == "exposure_plan"
    ]
    assert exposure_children
    assert min(sum(child.volume_weights) for child in exposure_children) < 0.04
    assert max(sum(child.volume_weights) for child in exposure_children) > 0.04


def test_checkpoint_replace_retries_transient_windows_file_lock(tmp_path, monkeypatch):
    source = tmp_path / "checkpoint.tmp"
    destination = tmp_path / "checkpoint.json"
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    real_replace = __import__("os").replace
    attempts = []

    def flaky_replace(left, right):
        attempts.append((left, right))
        if len(attempts) < 3:
            raise PermissionError("temporarily locked")
        return real_replace(left, right)

    monkeypatch.setattr("research.dubai_iterative.search.os.replace", flaky_replace)
    monkeypatch.setattr("research.dubai_iterative.search.time.sleep", lambda _seconds: None)

    _replace_checkpoint(source, destination)

    assert len(attempts) == 3
    assert destination.read_text(encoding="utf-8") == "new"
