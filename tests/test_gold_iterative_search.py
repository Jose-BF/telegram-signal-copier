from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json

import pytest

from research.dubai_iterative.contracts import (
    SearchBudget,
    SearchSpace,
    StrategyGenome,
)
from research.dubai_iterative.engine import SimulationResult
from research.dubai_iterative.evolution import Diagnosis
from research.dubai_iterative.search import (
    ChronologicalFold,
    classify_retrospective,
)
from research.gold_iterative.contracts import (
    gold_555_genome,
    gold_c490_genome,
)
from research.gold_iterative.folds import build_gold_fold_plan
from research.gold_iterative.search import run_gold_chronological_search


@dataclass(frozen=True)
class TinyPath:
    signal_id: str
    day: str


@dataclass(frozen=True)
class TinyGoldDataset:
    paths: tuple[TinyPath, ...]
    eligible_signal_ids: tuple[str, ...]
    eligible_signal_days: dict[str, str]
    exclusions: dict[str, tuple[str, ...]]
    source_hashes: dict[str, str]


def _dataset_with_incomplete_middle_day() -> TinyGoldDataset:
    return TinyGoldDataset(
        paths=(
            TinyPath("d1_a", "2026-08-24"),
            TinyPath("d1_b", "2026-08-24"),
            TinyPath("d2_a", "2026-08-25"),
            TinyPath("d3_a", "2026-08-26"),
            TinyPath("d3_b", "2026-08-26"),
            TinyPath("d4_a", "2026-08-27"),
        ),
        eligible_signal_ids=(
            "d1_a",
            "d1_b",
            "d2_a",
            "d2_missing",
            "d3_a",
            "d3_b",
            "d4_a",
        ),
        eligible_signal_days={
            "d1_a": "2026-08-24",
            "d1_b": "2026-08-24",
            "d2_a": "2026-08-25",
            "d2_missing": "2026-08-25",
            "d3_a": "2026-08-26",
            "d3_b": "2026-08-26",
            "d4_a": "2026-08-27",
        },
        exclusions={"tick_replay_blocked": ("d2_missing",)},
        source_hashes={"fixture": "complete-day-contract"},
    )


def _complete_dataset() -> TinyGoldDataset:
    return TinyGoldDataset(
        paths=(
            TinyPath("dev_1", "2026-08-24"),
            TinyPath("dev_2_a", "2026-08-25"),
            TinyPath("dev_2_b", "2026-08-25"),
            TinyPath("challenge", "2026-08-26"),
        ),
        eligible_signal_ids=("dev_1", "dev_2_a", "dev_2_b", "challenge"),
        eligible_signal_days={
            "dev_1": "2026-08-24",
            "dev_2_a": "2026-08-25",
            "dev_2_b": "2026-08-25",
            "challenge": "2026-08-26",
        },
        exclusions={},
        source_hashes={"fixture": "complete"},
    )


def _flat_evaluator(path, genome):
    return SimulationResult(
        signal_id=path.signal_id,
        strategy_fingerprint=genome.fingerprint,
        confidence_layer="fixture",
        entries=(),
        exits=(),
        pnl_eur=Decimal("1.00"),
        exit_reason="fixture",
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


class RecordingCritic:
    def __init__(self):
        self.signal_ids: list[str] = []

    def diagnose(self, evaluation):
        self.signal_ids.extend(
            result.signal_id for _day, result in evaluation.results
        )
        return Diagnosis(("no_dominant_failure",), ())


def test_fold_plan_excludes_any_incomplete_day_without_splitting_baskets():
    plan = build_gold_fold_plan(_dataset_with_incomplete_middle_day())

    assert plan.complete_days == (
        "2026-08-24",
        "2026-08-26",
        "2026-08-27",
    )
    assert plan.incomplete_days == ("2026-08-25",)
    assert len(plan.folds) == 1
    fold = plan.folds[0]
    assert fold.development_days == ("2026-08-24", "2026-08-26")
    assert fold.challenge_days == ("2026-08-27",)
    assert fold.development_contains("2026-08-26")
    assert not fold.development_contains("2026-08-25")
    assert not fold.challenge_contains("2026-08-25")

    day_3 = [
        path.signal_id
        for path in _dataset_with_incomplete_middle_day().paths
        if fold.development_contains(path.day) and path.day == "2026-08-26"
    ]
    assert day_3 == ["d3_a", "d3_b"]


def test_fold_plan_refuses_to_search_without_two_development_days_and_one_later_day():
    dataset = _complete_dataset()
    shortened = TinyGoldDataset(
        paths=dataset.paths[:-1],
        eligible_signal_ids=dataset.eligible_signal_ids[:-1],
        eligible_signal_days={
            key: value
            for key, value in dataset.eligible_signal_days.items()
            if key != "challenge"
        },
        exclusions={},
        source_hashes=dataset.source_hashes,
    )

    with pytest.raises(ValueError, match="at least 3 complete trading days"):
        build_gold_fold_plan(shortened)


def test_explicit_day_fold_rejects_inconsistent_or_overlapping_membership():
    with pytest.raises(ValueError, match="sorted and unique"):
        ChronologicalFold(
            "bad",
            "2026-08-24",
            "2026-08-26",
            "2026-08-27",
            "2026-08-27",
            development_days=("2026-08-26", "2026-08-24"),
            challenge_days=("2026-08-27",),
        )


def test_gold_search_uses_gold_anchors_and_never_leaks_challenge_into_critic(
    tmp_path,
):
    dataset = _complete_dataset()
    plan = build_gold_fold_plan(dataset)
    critic = RecordingCritic()
    first_generation: list[StrategyGenome] = []

    report = run_gold_chronological_search(
        dataset,
        fold_plan=plan,
        budget=SearchBudget(max_generations=2, patience_generations=10),
        search_space=SearchSpace(),
        output_dir=tmp_path,
        evaluator=_flat_evaluator,
        critic=critic,
        population_size=12,
        experiment_context={"channel": "wrong", "note": "fixture"},
        evaluation_callback=lambda _fold, generation, rows: (
            first_generation.extend(item.genome for item in rows)
            if generation == 1
            else None
        ),
    )

    assert report.fold_plan == plan
    assert first_generation
    assert {gold_555_genome().fingerprint, gold_c490_genome().fingerprint} <= {
        genome.fingerprint for genome in first_generation
    }
    assert all(genome.schema_version == 2 for genome in first_generation)
    assert set(critic.signal_ids) == {"dev_1", "dev_2_a", "dev_2_b"}
    challenge_ids = {
        result.signal_id
        for fold_report in report.search.fold_reports
        for evaluation in fold_report.challenge_evaluations
        for _day, result in evaluation.results
    }
    assert challenge_ids == {"challenge"}
    checkpoint = json.loads(
        report.search.fold_reports[0].checkpoint_path.read_text(encoding="utf-8")
    )
    assert checkpoint["experiment_context"]["channel"] == "canal2"
    assert checkpoint["experiment_context"]["note"] == "fixture"


def test_gold_development_profit_that_fails_later_is_not_a_winner():
    assessment = classify_retrospective(
        train_net=Decimal("555.00"),
        challenge_net=Decimal("-490.00"),
    )

    assert assessment.confidence == "retrospective_unstable"
    assert assessment.promotion_eligible is False
