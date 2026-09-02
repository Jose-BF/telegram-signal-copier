"""Finite, resumable and chronologically isolated strategy search."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
import json
import os
from pathlib import Path
import time
from typing import Callable, Mapping, Protocol, Sequence

import numpy as np

from .contracts import SearchBudget, SearchSpace, StrategyGenome
from .engine import SimulationResult, simulate
from .evolution import (
    CandidateEvaluation,
    Critic,
    Mutator,
    collapse_observational_equivalents,
    crossover,
    deduplicate,
    evolve_generation,
    pareto_front,
    sample_diverse_population,
    seed_population,
)
from .refinement import parameter_neighborhood
from .robustness import (
    ExecutionRobustnessAssessment,
    ScenarioEvaluation,
    assess_execution_robustness,
    rank_robust_candidates,
)


CHECKPOINT_SCHEMA_VERSION = 3


class SearchDataset(Protocol):
    paths: Sequence[object]
    source_hashes: Mapping[str, str]


Evaluator = Callable[[object, StrategyGenome], SimulationResult]
Clock = Callable[[], float]


@dataclass(frozen=True)
class ChronologicalFold:
    name: str
    development_from: str
    development_to: str
    challenge_from: str
    challenge_to: str
    development_days: tuple[str, ...] = ()
    challenge_days: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("fold name cannot be empty")
        if self.development_from > self.development_to:
            raise ValueError("development range is reversed")
        if self.challenge_from > self.challenge_to:
            raise ValueError("challenge range is reversed")
        if self.development_to >= self.challenge_from:
            raise ValueError("development and challenge ranges must not overlap")
        for label, days, range_from, range_to in (
            (
                "development",
                self.development_days,
                self.development_from,
                self.development_to,
            ),
            (
                "challenge",
                self.challenge_days,
                self.challenge_from,
                self.challenge_to,
            ),
        ):
            if not days:
                continue
            if tuple(sorted(set(days))) != days:
                raise ValueError(f"{label} days must be sorted and unique")
            if days[0] != range_from or days[-1] != range_to:
                raise ValueError(f"{label} day bounds do not match its range")
        if set(self.development_days).intersection(self.challenge_days):
            raise ValueError("development and challenge day membership overlaps")

    def development_contains(self, day: str) -> bool:
        if self.development_days:
            return day in self.development_days
        return self.development_from <= day <= self.development_to

    def challenge_contains(self, day: str) -> bool:
        if self.challenge_days:
            return day in self.challenge_days
        return self.challenge_from <= day <= self.challenge_to


DEFAULT_DUBAI_FOLDS = (
    ChronologicalFold("fold_1", "2026-07-27", "2026-07-31", "2026-08-04", "2026-08-05"),
    ChronologicalFold("fold_2", "2026-07-27", "2026-08-05", "2026-08-06", "2026-08-07"),
    ChronologicalFold("fold_3", "2026-07-27", "2026-08-07", "2026-08-10", "2026-08-12"),
    ChronologicalFold("fold_4", "2026-07-27", "2026-08-12", "2026-08-13", "2026-08-14"),
)


@dataclass(frozen=True)
class RetrospectiveAssessment:
    confidence: str
    promotion_eligible: bool
    reason: str


@dataclass(frozen=True)
class SearchReport:
    fold: ChronologicalFold
    stop_reason: str
    generations_completed: int
    evaluations: int
    elapsed_seconds: float
    frontier: tuple[CandidateEvaluation, ...]
    challenge_evaluations: tuple[CandidateEvaluation, ...]
    checkpoint_path: Path
    stale_generations: int
    generation_summaries: tuple["GenerationProgress", ...]

    @property
    def frontier_fingerprints(self) -> tuple[str, ...]:
        return tuple(item.genome.fingerprint for item in self.frontier)


@dataclass(frozen=True)
class ChronologicalSearchReport:
    fold_reports: tuple[SearchReport, ...]

    @property
    def total_evaluations(self) -> int:
        return sum(item.evaluations for item in self.fold_reports)


@dataclass(frozen=True)
class CrossFoldCandidateValidation:
    assessments: tuple[ExecutionRobustnessAssessment, ...]
    eligible: tuple[ExecutionRobustnessAssessment, ...]
    rejected: tuple[ExecutionRobustnessAssessment, ...]

    @property
    def considered_count(self) -> int:
        return len(self.assessments)


@dataclass(frozen=True)
class _TemporalValidationContract:
    future_folds: tuple[ChronologicalFold, ...]
    validation_fold_names: tuple[str, ...]
    validation_days: tuple[str, ...]
    signal_count: int
    filled_signal_count: int
    participation_rate: float
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class GenerationProgress:
    fold: str
    generation: int
    max_generations: int
    evaluated: int
    max_evaluations: int
    frontier_size: int
    stale_generations: int
    elapsed_seconds: float


ProgressCallback = Callable[[GenerationProgress], None]
CandidateProgressCallback = Callable[[int, int], None]
EvaluationCallback = Callable[
    [ChronologicalFold, int, tuple[CandidateEvaluation, ...]],
    None,
]
PopulationFactory = Callable[..., Sequence[StrategyGenome]]
NeighborhoodFactory = Callable[
    [StrategyGenome, SearchSpace],
    Sequence[StrategyGenome],
]


def cross_validate_frontier_candidates(
    dataset: SearchDataset,
    report: ChronologicalSearchReport,
    *,
    evaluator: Evaluator = simulate,
    additional_execution_scenarios: Sequence[
        tuple[str, Evaluator]
    ] = (),
    minimum_participation: float = 0.50,
    minimum_positive_challenge_ratio: float = 1.0,
    minimum_future_challenge_folds: int = 1,
    minimum_future_challenge_signals: int = 1,
    minimum_future_filled_signals: int = 1,
    workers: int = 1,
    progress_callback: CandidateProgressCallback | None = None,
) -> CrossFoldCandidateValidation:
    """Freeze each rule at first discovery and test only later evidence."""

    folds = tuple(item.fold for item in report.fold_reports)
    for name, value in (
        ("minimum_future_challenge_folds", minimum_future_challenge_folds),
        ("minimum_future_challenge_signals", minimum_future_challenge_signals),
        ("minimum_future_filled_signals", minimum_future_filled_signals),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")

    genomes_by_fingerprint: dict[str, StrategyGenome] = {}
    discovery_index: dict[str, int] = {}
    for fold_index, fold_report in enumerate(report.fold_reports):
        for item in fold_report.frontier:
            fingerprint = item.genome.fingerprint
            if fingerprint not in genomes_by_fingerprint:
                genomes_by_fingerprint[fingerprint] = item.genome
                discovery_index[fingerprint] = fold_index
    genomes = tuple(genomes_by_fingerprint.values())
    scenario_evaluators = (
        ("full_window", evaluator),
        *tuple(additional_execution_scenarios),
    )
    scenario_names = tuple(name for name, _item in scenario_evaluators)
    if any(not str(name).strip() for name in scenario_names):
        raise ValueError("execution scenario names cannot be empty")
    if len(set(scenario_names)) != len(scenario_names):
        raise ValueError("execution scenario names must be unique")

    evaluations_by_fingerprint: dict[
        str, list[ScenarioEvaluation]
    ] = {item.fingerprint: [] for item in genomes}
    total = len(genomes) * len(scenario_evaluators)
    completed_before = 0
    active_fingerprints = set(evaluations_by_fingerprint)
    temporal_contracts: dict[str, _TemporalValidationContract] = {}
    for scenario_index, (name, scenario_evaluator) in enumerate(
        scenario_evaluators
    ):
        scenario_genomes = (
            genomes
            if scenario_index == 0
            else tuple(
                item for item in genomes
                if item.fingerprint in active_fingerprints
            )
        )
        callback = None
        if progress_callback is not None:
            callback = lambda completed, _subtotal, offset=completed_before: (
                progress_callback(offset + completed, total)
            )
        try:
            evaluations = _evaluate_population(
                scenario_genomes,
                tuple(dataset.paths),
                scenario_evaluator,
                workers=workers,
                progress_callback=callback,
            )
        finally:
            _release_evaluator_cache(scenario_evaluator)
        completed_before += len(scenario_genomes)
        for evaluation in evaluations:
            fingerprint = evaluation.genome.fingerprint
            evaluations_by_fingerprint[fingerprint].append(
                ScenarioEvaluation(name, evaluation)
            )
            if scenario_index == 0:
                temporal_contracts[fingerprint] = _temporal_contract(
                    evaluation,
                    folds=folds,
                    discovery_index=discovery_index[fingerprint],
                    minimum_future_challenge_folds=(
                        minimum_future_challenge_folds
                    ),
                    minimum_future_challenge_signals=(
                        minimum_future_challenge_signals
                    ),
                    minimum_future_filled_signals=(
                        minimum_future_filled_signals
                    ),
                    minimum_participation=minimum_participation,
                )

        active_fingerprints = {
            fingerprint
            for fingerprint in active_fingerprints
            if (
                evaluations_by_fingerprint[fingerprint]
                and _can_survive_remaining_worlds(
                    evaluations_by_fingerprint[fingerprint][-1].evaluation,
                    minimum_participation=minimum_participation,
                )
                and not temporal_contracts[fingerprint].blockers
            )
        }

    assessments = []
    for genome in genomes:
        fingerprint = genome.fingerprint
        temporal = temporal_contracts[fingerprint]
        scenarios = tuple(evaluations_by_fingerprint[fingerprint])
        selection_blockers = list(temporal.blockers)
        if len(scenarios) != len(scenario_evaluators):
            selection_blockers.append("not_all_execution_worlds_evaluated")
        assessment = assess_execution_robustness(
            scenarios,
            minimum_participation=minimum_participation,
            folds=temporal.future_folds,
            minimum_positive_challenge_ratio=(
                minimum_positive_challenge_ratio
            ),
        )
        selection_blockers = tuple(dict.fromkeys(selection_blockers))
        assessments.append(replace(
            assessment,
            discovery_fold_name=folds[
                discovery_index[fingerprint]
            ].name,
            validation_fold_names=temporal.validation_fold_names,
            validation_days=temporal.validation_days,
            validation_signal_count=temporal.signal_count,
            validation_filled_signal_count=temporal.filled_signal_count,
            validation_participation_rate=temporal.participation_rate,
            selection_blockers=selection_blockers,
            evidence_complete=(
                assessment.evidence_complete and not selection_blockers
            ),
            robustness_eligible=(
                assessment.robustness_eligible and not selection_blockers
            ),
        ))
    ranked = rank_robust_candidates(tuple(assessments))
    eligible = tuple(item for item in ranked if item.robustness_eligible)
    rejected = tuple(item for item in ranked if not item.robustness_eligible)
    return CrossFoldCandidateValidation(
        assessments=ranked,
        eligible=eligible,
        rejected=rejected,
    )


def _temporal_contract(
    evaluation: CandidateEvaluation,
    *,
    folds: Sequence[ChronologicalFold],
    discovery_index: int,
    minimum_future_challenge_folds: int,
    minimum_future_challenge_signals: int,
    minimum_future_filled_signals: int,
    minimum_participation: float,
) -> _TemporalValidationContract:
    future_folds = tuple(folds[discovery_index:])
    challenge_rows = tuple(
        (str(day), result)
        for day, result in evaluation.results
        if any(fold.challenge_contains(str(day)) for fold in future_folds)
    )
    covered_folds = tuple(
        fold
        for fold in future_folds
        if any(
            fold.challenge_contains(day)
            for day, _result in challenge_rows
        )
    )
    validation_days = tuple(sorted({day for day, _result in challenge_rows}))
    filled_signal_count = sum(
        not result.unfilled for _day, result in challenge_rows
    )
    participation_rate = (
        filled_signal_count / len(challenge_rows)
        if challenge_rows
        else 0.0
    )
    blockers: list[str] = []
    if len(covered_folds) < minimum_future_challenge_folds:
        blockers.append("insufficient_future_challenge_folds")
    if len(challenge_rows) < minimum_future_challenge_signals:
        blockers.append("insufficient_future_challenge_signals")
    if filled_signal_count < minimum_future_filled_signals:
        blockers.append("insufficient_future_filled_signals")
    if participation_rate < minimum_participation:
        blockers.append("future_participation_below_threshold")
    return _TemporalValidationContract(
        future_folds=future_folds,
        validation_fold_names=tuple(fold.name for fold in covered_folds),
        validation_days=validation_days,
        signal_count=len(challenge_rows),
        filled_signal_count=filled_signal_count,
        participation_rate=participation_rate,
        blockers=tuple(blockers),
    )


def _can_survive_remaining_worlds(
    evaluation: CandidateEvaluation,
    *,
    minimum_participation: float,
) -> bool:
    return (
        not evaluation.blockers
        and evaluation.net_eur is not None
        and evaluation.net_eur > 0
        and evaluation.max_drawdown_eur is not None
        and evaluation.normalized_net_per_001 is not None
        and evaluation.normalized_max_drawdown_per_001 is not None
        and evaluation.participation_rate >= minimum_participation
    )


def _release_evaluator_cache(evaluator: object) -> None:
    clear_cache = getattr(evaluator, "clear_cache", None)
    if callable(clear_cache):
        clear_cache()


class SearchCheckpointError(ValueError):
    """Raised when a checkpoint does not describe the current experiment."""


def classify_retrospective(
    *,
    train_net: Decimal,
    challenge_net: Decimal,
) -> RetrospectiveAssessment:
    if train_net > 0 and challenge_net <= 0:
        return RetrospectiveAssessment(
            confidence="retrospective_unstable",
            promotion_eligible=False,
            reason="development profit did not survive the chronological challenge",
        )
    if train_net <= 0:
        return RetrospectiveAssessment(
            confidence="retrospective_negative",
            promotion_eligible=False,
            reason="development result is not positive",
        )
    return RetrospectiveAssessment(
        confidence="demo_candidate",
        promotion_eligible=False,
        reason="positive retrospective evidence still needs untouched forward proof",
    )


def run_chronological_search(
    dataset: SearchDataset,
    *,
    folds: Sequence[ChronologicalFold] = DEFAULT_DUBAI_FOLDS,
    budget: SearchBudget,
    search_space: SearchSpace,
    output_dir: Path,
    evaluator: Evaluator = simulate,
    critic: Critic | None = None,
    mutator: Mutator | None = None,
    seed: int = 20260817,
    population_size: int = 64,
    clock: Clock = time.monotonic,
    progress_callback: ProgressCallback | None = None,
    evaluation_callback: EvaluationCallback | None = None,
    experiment_context: Mapping[str, object] | None = None,
    workers: int = 1,
    initial_genomes: Sequence[StrategyGenome] = (),
    seed_population_factory: PopulationFactory = seed_population,
    scout_population_factory: PopulationFactory = sample_diverse_population,
    neighborhood_factory: NeighborhoodFactory = parameter_neighborhood,
    baseline_genome: StrategyGenome | None = None,
    resume_from_root: Path | None = None,
) -> ChronologicalSearchReport:
    """Run each expanding fold independently with its own frozen challenge."""

    reports = []
    for fold in folds:
        resume_from = None
        if resume_from_root is not None:
            candidate = Path(resume_from_root) / fold.name / "checkpoint.json"
            if candidate.is_file():
                resume_from = candidate
        reports.append(run_search(
            dataset,
            fold=fold,
            budget=budget,
            search_space=search_space,
            output_dir=Path(output_dir) / fold.name,
            evaluator=evaluator,
            critic=critic,
            mutator=mutator,
            seed=seed,
            population_size=population_size,
            clock=clock,
            progress_callback=progress_callback,
            evaluation_callback=evaluation_callback,
            experiment_context=experiment_context,
            workers=workers,
            initial_genomes=initial_genomes,
            seed_population_factory=seed_population_factory,
            scout_population_factory=scout_population_factory,
            neighborhood_factory=neighborhood_factory,
            baseline_genome=baseline_genome,
            resume_from=resume_from,
        ))
    return ChronologicalSearchReport(tuple(reports))


def run_search(
    dataset: SearchDataset,
    *,
    fold: ChronologicalFold,
    budget: SearchBudget,
    search_space: SearchSpace,
    output_dir: Path,
    evaluator: Evaluator = simulate,
    critic: Critic | None = None,
    mutator: Mutator | None = None,
    seed: int = 20260817,
    population_size: int = 64,
    resume_from: Path | None = None,
    clock: Clock = time.monotonic,
    progress_callback: ProgressCallback | None = None,
    evaluation_callback: EvaluationCallback | None = None,
    experiment_context: Mapping[str, object] | None = None,
    workers: int = 1,
    initial_genomes: Sequence[StrategyGenome] = (),
    seed_population_factory: PopulationFactory = seed_population,
    scout_population_factory: PopulationFactory = sample_diverse_population,
    neighborhood_factory: NeighborhoodFactory = parameter_neighborhood,
    baseline_genome: StrategyGenome | None = None,
) -> SearchReport:
    """Run one development fold; challenge rows remain invisible until stop."""

    if population_size <= 0:
        raise ValueError("population_size must be positive")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers <= 0:
        raise ValueError("workers must be a positive integer")
    initial_genomes = deduplicate(initial_genomes)
    baseline = baseline_genome or StrategyGenome.baseline()
    baseline_errors = (
        *baseline.validation_errors(),
        *search_space.validation_errors(baseline),
    )
    if baseline_errors:
        raise ValueError(
            "invalid baseline genome "
            f"{baseline.fingerprint[:12]}:"
            f"{','.join(sorted(set(baseline_errors)))}"
        )
    if len(initial_genomes) > population_size:
        raise ValueError("initial_genomes cannot exceed population_size")
    for genome in initial_genomes:
        errors = (*genome.validation_errors(), *search_space.validation_errors(genome))
        if errors:
            raise ValueError(
                "invalid initial genome "
                f"{genome.fingerprint[:12]}: {','.join(sorted(set(errors)))}"
            )
    context = dict(experiment_context or {})
    if initial_genomes:
        context["initial_genome_fingerprints"] = sorted(
            item.fingerprint for item in initial_genomes
        )
    context["search_operators"] = {
        "seed_population": _callable_identity(seed_population_factory),
        "scout_population": _callable_identity(scout_population_factory),
        "neighborhood": _callable_identity(neighborhood_factory),
        "critic": _callable_identity(critic),
        "mutator": _callable_identity(mutator),
        "baseline_fingerprint": baseline.fingerprint,
    }
    experiment_context = _normalize_experiment_context(context)
    development_paths = tuple(
        path for path in dataset.paths if fold.development_contains(str(path.day))
    )
    challenge_paths = tuple(
        path for path in dataset.paths if fold.challenge_contains(str(path.day))
    )
    if not development_paths:
        raise ValueError(f"fold {fold.name} has no development paths")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "checkpoint.json"
    rng = np.random.default_rng(seed)
    start_clock = clock()
    carried_elapsed = 0.0
    generation_completed = 0
    evaluations = 0
    stale_generations = 0
    archive: tuple[CandidateEvaluation, ...] = ()
    seen: set[str] = set()
    generation_summaries: list[GenerationProgress] = []
    resumed_stop_reason: str | None = None

    if resume_from is not None:
        state = _load_checkpoint(
            Path(resume_from),
            dataset=dataset,
            fold=fold,
            search_space=search_space,
            seed=seed,
            experiment_context=experiment_context,
        )
        generation_completed = int(state["generations_completed"])
        evaluations = int(state["evaluations"])
        stale_generations = int(state["stale_generations"])
        carried_elapsed = float(state["elapsed_seconds"])
        seen = set(state["seen_fingerprints"])
        population = tuple(
            StrategyGenome.from_dict(item) for item in state["next_population"]
        )
        archive_genomes = tuple(
            StrategyGenome.from_dict(item) for item in state["archive_genomes"]
        )
        archive = _evaluate_population(
            archive_genomes,
            development_paths,
            evaluator,
            workers=workers,
        )
        rng.bit_generator.state = state["numpy_random_state"]
        generation_summaries = [
            GenerationProgress(**item)
            for item in state.get("generation_summaries", ())
        ]
        resumed_stop_reason = (
            str(state["stop_reason"])
            if state.get("stop_reason") is not None
            else None
        )
    else:
        seeds = tuple(seed_population_factory(search_space, seed=seed))
        scout_slots = min(
            max(1, population_size // 3),
            max(0, population_size - 1),
        )
        scout_pool = tuple(scout_population_factory(
            search_space,
            seed=seed + 91_337,
            count=max(scout_slots, scout_slots * 3),
        ))
        scouts = tuple(sorted(
            scout_pool,
            key=lambda item: (
                -_distance_from_baseline(item, baseline),
                item.fingerprint,
            ),
        )[:scout_slots])
        population = deduplicate((
            *initial_genomes,
            baseline,
            *tuple(
                item
                for item in seeds
                if item.fingerprint != baseline.fingerprint
            )[: max(0, population_size - len(scouts) - 1)],
            *scouts,
        ))[:population_size]
        seen.update(item.fingerprint for item in population)

    stop_reason: str | None = None
    next_population = population
    try:
        for generation_index in range(generation_completed, budget.max_generations):
            elapsed = _elapsed(clock, start_clock, carried_elapsed)
            deepest = _deepest_lineage(archive, next_population)
            stop_reason = budget.stop_reason(
                generation=generation_completed,
                evaluations=evaluations,
                elapsed_seconds=elapsed,
                stale_generations=stale_generations,
                deepest_lineage=deepest,
            )
            if stop_reason is not None:
                break
            if not next_population:
                stop_reason = "population_exhausted"
                break

            remaining = budget.max_evaluations - evaluations
            if remaining <= 0:
                stop_reason = "max_evaluations"
                break
            current_population = tuple(next_population[:remaining])
            current = _evaluate_population(
                current_population,
                development_paths,
                evaluator,
                workers=workers,
            )
            evaluations += len(current)
            if evaluation_callback is not None:
                evaluation_callback(fold, generation_index + 1, current)
            current = collapse_observational_equivalents(current)
            previous_archive = archive
            archive = pareto_front(collapse_observational_equivalents(
                _merge_evaluations(archive, current)
            ))
            if _material_frontier_improvement(previous_archive, archive):
                stale_generations = 0
            else:
                stale_generations += 1
            generation_completed = generation_index + 1

            next_population = _next_population(
                archive=archive,
                current=current,
                critic=critic,
                mutator=mutator,
                search_space=search_space,
                seeds=tuple(seed_population_factory(search_space, seed=seed)),
                seen=seen,
                rng=rng,
                seed=seed + generation_completed * 10_000,
                population_size=population_size,
                max_lineage_depth=budget.max_lineage_depth,
                scout_population_factory=scout_population_factory,
                neighborhood_factory=neighborhood_factory,
                baseline_genome=baseline,
            )
            elapsed = _elapsed(clock, start_clock, carried_elapsed)
            progress = GenerationProgress(
                fold=fold.name,
                generation=generation_completed,
                max_generations=budget.max_generations,
                evaluated=evaluations,
                max_evaluations=budget.max_evaluations,
                frontier_size=len(archive),
                stale_generations=stale_generations,
                elapsed_seconds=elapsed,
            )
            generation_summaries.append(progress)
            if progress_callback is not None:
                progress_callback(progress)
            deepest = _deepest_lineage(archive, next_population)
            stop_reason = budget.stop_reason(
                generation=generation_completed,
                evaluations=evaluations,
                elapsed_seconds=elapsed,
                stale_generations=stale_generations,
                deepest_lineage=deepest,
            )
            _write_checkpoint(
                checkpoint_path,
                dataset=dataset,
                fold=fold,
                search_space=search_space,
                seed=seed,
                experiment_context=experiment_context,
                generations_completed=generation_completed,
                evaluations=evaluations,
                stale_generations=stale_generations,
                elapsed_seconds=elapsed,
                seen=seen,
                next_population=next_population,
                archive=archive,
                rng=rng,
                stop_reason=stop_reason,
                generation_summaries=generation_summaries,
            )
            if stop_reason is not None:
                break
        if stop_reason is None:
            stop_reason = (
                resumed_stop_reason
                if (
                    resumed_stop_reason is not None
                    and generation_completed >= budget.max_generations
                )
                else "completed"
            )
    except KeyboardInterrupt:
        stop_reason = "user_interrupt"

    elapsed = _elapsed(clock, start_clock, carried_elapsed)
    _write_checkpoint(
        checkpoint_path,
        dataset=dataset,
        fold=fold,
        search_space=search_space,
        seed=seed,
        experiment_context=experiment_context,
        generations_completed=generation_completed,
        evaluations=evaluations,
        stale_generations=stale_generations,
        elapsed_seconds=elapsed,
        seen=seen,
        next_population=next_population,
        archive=archive,
        rng=rng,
        stop_reason=stop_reason,
        generation_summaries=generation_summaries,
    )

    challenge = _evaluate_population(
        tuple(item.genome for item in archive),
        challenge_paths,
        evaluator,
        workers=workers,
    ) if challenge_paths else ()
    return SearchReport(
        fold=fold,
        stop_reason=stop_reason,
        generations_completed=generation_completed,
        evaluations=evaluations,
        elapsed_seconds=elapsed,
        frontier=archive,
        challenge_evaluations=challenge,
        checkpoint_path=checkpoint_path,
        stale_generations=stale_generations,
        generation_summaries=tuple(generation_summaries),
    )


def _evaluate_population(
    genomes: Sequence[StrategyGenome],
    paths: Sequence[object],
    evaluator: Evaluator,
    *,
    workers: int,
    progress_callback: CandidateProgressCallback | None = None,
) -> tuple[CandidateEvaluation, ...]:
    def evaluate(genome: StrategyGenome) -> CandidateEvaluation:
        return CandidateEvaluation.from_results(
            genome,
            (
                (str(path.day), evaluator(path, genome))
                for path in paths
            ),
        )

    if workers == 1 or len(genomes) < 2:
        results = []
        for index, genome in enumerate(genomes, start=1):
            results.append(evaluate(genome))
            if progress_callback is not None:
                progress_callback(index, len(genomes))
        return tuple(results)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = []
        for index, evaluation in enumerate(
            pool.map(evaluate, genomes), start=1
        ):
            results.append(evaluation)
            if progress_callback is not None:
                progress_callback(index, len(genomes))
        return tuple(results)


def _next_population(
    *,
    archive: Sequence[CandidateEvaluation],
    current: Sequence[CandidateEvaluation],
    critic: Critic | None,
    mutator: Mutator | None = None,
    search_space: SearchSpace,
    seeds: Sequence[StrategyGenome],
    seen: set[str],
    rng: np.random.Generator,
    seed: int,
    population_size: int,
    max_lineage_depth: int,
    scout_population_factory: PopulationFactory = sample_diverse_population,
    neighborhood_factory: NeighborhoodFactory = parameter_neighborhood,
    baseline_genome: StrategyGenome | None = None,
) -> tuple[StrategyGenome, ...]:
    proposals: list[StrategyGenome] = []
    parents = tuple(archive[: min(8, len(archive))]) or tuple(current[:1])
    for parent in parents:
        neighborhood = tuple(neighborhood_factory(parent.genome, search_space))
        exposure = sorted(
            (
                item for item in neighborhood
                if item.mutation_reason == "exposure_plan"
            ),
            key=lambda item: (sum(item.volume_weights), item.fingerprint),
        )
        if exposure:
            proposals.append(exposure[0])
            if exposure[-1].fingerprint != exposure[0].fingerprint:
                proposals.append(exposure[-1])
        proposals.extend(
            item for item in neighborhood
            if item.mutation_reason != "exposure_plan"
        )
        proposals.extend(exposure[1:-1])
    batch = evolve_generation(
        parents,
        critic=critic,
        mutator=mutator,
        search_space=search_space,
        seed=seed,
    )
    proposals.extend(batch.children)

    if len(parents) > 1:
        order = rng.permutation(len(parents)).tolist()
        for offset in range(0, len(order) - 1, 2):
            left = parents[order[offset]].genome
            right = parents[order[offset + 1]].genome
            proposals.extend(crossover(
                left,
                right,
                search_space=search_space,
                seed=seed + offset,
            ))
    proposals.extend(seeds)

    scout_slots = max(1, population_size // 3)
    scouts = tuple(scout_population_factory(
        search_space,
        seed=seed + 91_337,
        count=max(scout_slots, scout_slots * 4),
    ))

    accepted: list[StrategyGenome] = []
    for genome in deduplicate(proposals):
        if len(accepted) >= max(0, population_size - scout_slots):
            break
        if genome.fingerprint in seen:
            continue
        if genome.lineage_depth > max_lineage_depth:
            continue
        if genome.validation_errors() or search_space.validation_errors(genome):
            continue
        accepted.append(genome)
        seen.add(genome.fingerprint)
    for genome in sorted(
        scouts,
        key=lambda item: (
            -_distance_from_baseline(item, baseline_genome),
            item.fingerprint,
        ),
    ):
        if len(accepted) >= population_size:
            break
        if genome.fingerprint in seen:
            continue
        if genome.validation_errors() or search_space.validation_errors(genome):
            continue
        accepted.append(genome)
        seen.add(genome.fingerprint)
    return tuple(accepted)


def _distance_from_baseline(
    genome: StrategyGenome,
    baseline_genome: StrategyGenome | None = None,
) -> int:
    baseline = baseline_genome or StrategyGenome.baseline()
    blocks = (
        (
            "entry_mode",
            "entry_value",
            "entry_confirmation_value",
            "entry_expiry_min",
            "entry_ladder_mode",
            "entry_ladder_step",
            "pending_entry_policy",
        ),
        ("leg_count", "volume_weights"),
        (
            "target_mode",
            "target_value",
            "target_steps",
            "partial_fraction",
            "runner_target",
        ),
        ("be_mode", "be_trigger"),
        (
            "stop_mode",
            "stop_value",
            "hard_stop_eur_per_leg",
            "trailing_distance",
        ),
        ("profit_lock_arm", "profit_lock_giveback"),
        ("time_exit_min", "time_exit_mode"),
        ("provider_management_mode",),
        ("context_filter_mode", "context_filter_value"),
    )
    return sum(
        any(getattr(genome, name) != getattr(baseline, name) for name in block)
        for block in blocks
    )


def _callable_identity(value: object) -> str | None:
    if value is None:
        return None
    target = value if isinstance(value, type) else getattr(value, "__class__", None)
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if module and qualname:
        return f"{module}.{qualname}"
    if target is not None:
        return f"{target.__module__}.{target.__qualname__}"
    return str(type(value))


def _merge_evaluations(
    left: Sequence[CandidateEvaluation],
    right: Sequence[CandidateEvaluation],
) -> tuple[CandidateEvaluation, ...]:
    merged = {item.genome.fingerprint: item for item in left}
    for item in right:
        merged[item.genome.fingerprint] = item
    return tuple(merged.values())


def _material_frontier_improvement(
    previous: Sequence[CandidateEvaluation],
    current: Sequence[CandidateEvaluation],
) -> bool:
    if not current:
        return False
    if not previous:
        return True
    old_vectors = {_objective_vector(item) for item in previous}
    return any(_objective_vector(item) not in old_vectors for item in current)


def _objective_vector(item: CandidateEvaluation) -> tuple[object, ...]:
    return (
        item.normalized_net_per_001,
        item.normalized_max_drawdown_per_001,
        item.normalized_worst_day_per_001,
        None if item.positive_day_concentration is None else round(item.positive_day_concentration, 6),
        item.complexity,
        round(item.max_signal_exposure, 10),
    )


def _deepest_lineage(
    archive: Sequence[CandidateEvaluation],
    population: Sequence[StrategyGenome],
) -> int:
    depths = [item.genome.lineage_depth for item in archive]
    depths.extend(item.lineage_depth for item in population)
    return max(depths, default=0)


def _elapsed(clock: Clock, start: float, carried: float) -> float:
    return carried + max(0.0, float(clock()) - float(start))


def _write_checkpoint(
    path: Path,
    *,
    dataset: SearchDataset,
    fold: ChronologicalFold,
    search_space: SearchSpace,
    seed: int,
    experiment_context: Mapping[str, object],
    generations_completed: int,
    evaluations: int,
    stale_generations: int,
    elapsed_seconds: float,
    seen: set[str],
    next_population: Sequence[StrategyGenome],
    archive: Sequence[CandidateEvaluation],
    rng: np.random.Generator,
    stop_reason: str | None,
    generation_summaries: Sequence[GenerationProgress],
) -> None:
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "source_hashes": dict(sorted(dataset.source_hashes.items())),
        "fold": asdict(fold),
        "search_space": asdict(search_space),
        "seed": seed,
        "experiment_context": experiment_context,
        "generations_completed": generations_completed,
        "evaluations": evaluations,
        "stale_generations": stale_generations,
        "elapsed_seconds": elapsed_seconds,
        "seen_fingerprints": sorted(seen),
        "next_population": [item.to_dict() for item in next_population],
        "archive_genomes": [item.genome.to_dict() for item in archive],
        "numpy_random_state": rng.bit_generator.state,
        "stop_reason": stop_reason,
        "generation_summaries": [asdict(item) for item in generation_summaries],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    _replace_checkpoint(temporary, path)


def _replace_checkpoint(
    temporary: Path,
    destination: Path,
    *,
    attempts: int = 6,
) -> None:
    """Survive short antivirus/OneDrive locks without hiding a real failure."""

    if attempts <= 0:
        raise ValueError("attempts must be positive")
    for attempt in range(attempts):
        try:
            os.replace(temporary, destination)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.05 * (2 ** attempt))


def _load_checkpoint(
    path: Path,
    *,
    dataset: SearchDataset,
    fold: ChronologicalFold,
    search_space: SearchSpace,
    seed: int,
    experiment_context: Mapping[str, object],
) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SearchCheckpointError(f"cannot read checkpoint: {exc}") from exc
    if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise SearchCheckpointError("checkpoint schema version does not match")
    if payload.get("source_hashes") != dict(sorted(dataset.source_hashes.items())):
        raise SearchCheckpointError("checkpoint source hashes do not match")
    if payload.get("fold") != _normalize_json_mapping(asdict(fold)):
        raise SearchCheckpointError("checkpoint fold does not match")
    if payload.get("search_space") != asdict(search_space):
        raise SearchCheckpointError("checkpoint search space does not match")
    if payload.get("seed") != seed:
        raise SearchCheckpointError("checkpoint seed does not match")
    if payload.get("experiment_context") != experiment_context:
        raise SearchCheckpointError("checkpoint experiment context does not match")
    return payload


def _normalize_experiment_context(
    context: Mapping[str, object] | None,
) -> dict[str, object]:
    return _normalize_json_mapping(dict(context or {}))


def _normalize_json_mapping(
    value: Mapping[str, object],
) -> dict[str, object]:
    return json.loads(json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ))
