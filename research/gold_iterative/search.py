"""Gold Signals chronological strategy-search coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Mapping, Sequence

from research.dubai_iterative.contracts import (
    SearchBudget,
    SearchSpace,
    StrategyGenome,
)
from research.dubai_iterative.evolution import (
    Critic,
    Mutator,
    diagnose_gold,
    mutate_gold_from_diagnosis,
)
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.refinement import parameter_neighborhood
from research.dubai_iterative.search import (
    ChronologicalSearchReport,
    Clock,
    EvaluationCallback,
    Evaluator,
    ProgressCallback,
    run_chronological_search,
)

from .contracts import gold_555_genome, gold_c490_genome
from .folds import GoldFoldDataset, GoldFoldPlan, build_gold_fold_plan
from .seeds import gold_seed_population, sample_gold_population


class _GoldCritic:
    def diagnose(self, evaluation):
        return diagnose_gold(evaluation)


@dataclass(frozen=True)
class GoldSearchReport:
    fold_plan: GoldFoldPlan
    search: ChronologicalSearchReport


def run_gold_chronological_search(
    dataset: GoldFoldDataset,
    *,
    budget: SearchBudget,
    search_space: SearchSpace,
    output_dir: Path,
    fold_plan: GoldFoldPlan | None = None,
    evaluator: Evaluator | None = None,
    critic: Critic | None = None,
    mutator: Mutator | None = None,
    seed: int = 20260902,
    population_size: int = 64,
    clock: Clock = time.monotonic,
    progress_callback: ProgressCallback | None = None,
    evaluation_callback: EvaluationCallback | None = None,
    experiment_context: Mapping[str, object] | None = None,
    workers: int = 1,
    initial_genomes: Sequence[StrategyGenome] = (),
) -> GoldSearchReport:
    """Run the shared engine with Gold-only evidence and search operators."""

    active_plan = fold_plan or build_gold_fold_plan(dataset)
    active_evaluator = evaluator or FastEvaluator()
    anchors = (
        gold_555_genome(),
        gold_c490_genome(),
        *initial_genomes,
    )
    context = {
        **dict(experiment_context or {}),
        "channel": "canal2",
        "signal_scope": "formal_telegram_now",
        "day_partition": "complete_explicit_days_v1",
        "strategy_grammar": "gold_schema_v2",
    }
    search = run_chronological_search(
        dataset,
        folds=active_plan.folds,
        budget=budget,
        search_space=search_space,
        output_dir=Path(output_dir),
        evaluator=active_evaluator,
        critic=critic or _GoldCritic(),
        mutator=mutator or mutate_gold_from_diagnosis,
        seed=seed,
        population_size=population_size,
        clock=clock,
        progress_callback=progress_callback,
        evaluation_callback=evaluation_callback,
        experiment_context=context,
        workers=workers,
        initial_genomes=anchors,
        seed_population_factory=gold_seed_population,
        scout_population_factory=sample_gold_population,
        neighborhood_factory=parameter_neighborhood,
        baseline_genome=gold_555_genome(),
    )
    return GoldSearchReport(fold_plan=active_plan, search=search)
