from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from research.dubai_iterative.contracts import SearchBudget, SearchSpace, StrategyGenome
from research.dubai_iterative.engine import SimulationResult
from research.dubai_iterative.evolution import Diagnosis
from research.dubai_iterative.search import (
    ChronologicalFold,
    DEFAULT_DUBAI_FOLDS,
    SearchCheckpointError,
    classify_retrospective,
    run_chronological_search,
    run_search,
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
