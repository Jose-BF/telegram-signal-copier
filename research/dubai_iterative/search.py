"""Finite, resumable and chronologically isolated strategy search."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
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


CHECKPOINT_SCHEMA_VERSION = 2


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

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("fold name cannot be empty")
        if self.development_from > self.development_to:
            raise ValueError("development range is reversed")
        if self.challenge_from > self.challenge_to:
            raise ValueError("challenge range is reversed")
        if self.development_to >= self.challenge_from:
            raise ValueError("development and challenge ranges must not overlap")

    def development_contains(self, day: str) -> bool:
        return self.development_from <= day <= self.development_to

    def challenge_contains(self, day: str) -> bool:
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


def cross_validate_frontier_candidates(
    dataset: SearchDataset,
    report: ChronologicalSearchReport,
    *,
    evaluator: Evaluator = simulate,
    minimum_participation: float = 0.50,
    workers: int = 1,
    progress_callback: CandidateProgressCallback | None = None,
) -> CrossFoldCandidateValidation:
    """Freeze every discovered frontier rule and retest all later folds."""

    folds = tuple(item.fold for item in report.fold_reports)
    genomes = deduplicate(
        item.genome
        for fold_report in report.fold_reports
        for item in fold_report.frontier
    )
    evaluations = _evaluate_population(
        genomes,
        tuple(dataset.paths),
        evaluator,
        workers=workers,
        progress_callback=progress_callback,
    )
    assessments = tuple(
        assess_execution_robustness(
            (ScenarioEvaluation("full_window", evaluation),),
            minimum_participation=minimum_participation,
            folds=folds,
            minimum_positive_challenge_ratio=1.0,
        )
        for evaluation in evaluations
    )
    ranked = rank_robust_candidates(assessments)
    eligible = tuple(item for item in ranked if item.robustness_eligible)
    rejected = tuple(item for item in ranked if not item.robustness_eligible)
    return CrossFoldCandidateValidation(
        assessments=ranked,
        eligible=eligible,
        rejected=rejected,
    )


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
    seed: int = 20260817,
    population_size: int = 64,
    clock: Clock = time.monotonic,
    progress_callback: ProgressCallback | None = None,
    evaluation_callback: EvaluationCallback | None = None,
    experiment_context: Mapping[str, object] | None = None,
    workers: int = 1,
    initial_genomes: Sequence[StrategyGenome] = (),
) -> ChronologicalSearchReport:
    """Run each expanding fold independently with its own frozen challenge."""

    reports = tuple(
        run_search(
            dataset,
            fold=fold,
            budget=budget,
            search_space=search_space,
            output_dir=Path(output_dir) / fold.name,
            evaluator=evaluator,
            critic=critic,
            seed=seed,
            population_size=population_size,
            clock=clock,
            progress_callback=progress_callback,
            evaluation_callback=evaluation_callback,
            experiment_context=experiment_context,
            workers=workers,
            initial_genomes=initial_genomes,
        )
        for fold in folds
    )
    return ChronologicalSearchReport(reports)


def run_search(
    dataset: SearchDataset,
    *,
    fold: ChronologicalFold,
    budget: SearchBudget,
    search_space: SearchSpace,
    output_dir: Path,
    evaluator: Evaluator = simulate,
    critic: Critic | None = None,
    seed: int = 20260817,
    population_size: int = 64,
    resume_from: Path | None = None,
    clock: Clock = time.monotonic,
    progress_callback: ProgressCallback | None = None,
    evaluation_callback: EvaluationCallback | None = None,
    experiment_context: Mapping[str, object] | None = None,
    workers: int = 1,
    initial_genomes: Sequence[StrategyGenome] = (),
) -> SearchReport:
    """Run one development fold; challenge rows remain invisible until stop."""

    if population_size <= 0:
        raise ValueError("population_size must be positive")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers <= 0:
        raise ValueError("workers must be a positive integer")
    initial_genomes = deduplicate(initial_genomes)
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
    else:
        seeds = seed_population(search_space, seed=seed)
        baseline = StrategyGenome.baseline()
        scout_slots = min(
            max(1, population_size // 3),
            max(0, population_size - 1),
        )
        scout_pool = sample_diverse_population(
            search_space,
            seed=seed + 91_337,
            count=max(scout_slots, scout_slots * 3),
        )
        scouts = tuple(sorted(
            scout_pool,
            key=lambda item: (-_distance_from_baseline(item), item.fingerprint),
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
            previous_archive = archive
            archive = pareto_front(_merge_evaluations(archive, current))
            if _material_frontier_improvement(previous_archive, archive):
                stale_generations = 0
            else:
                stale_generations += 1
            generation_completed = generation_index + 1

            next_population = _next_population(
                archive=archive,
                current=current,
                critic=critic,
                search_space=search_space,
                seeds=seed_population(search_space, seed=seed),
                seen=seen,
                rng=rng,
                seed=seed + generation_completed * 10_000,
                population_size=population_size,
                max_lineage_depth=budget.max_lineage_depth,
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
            stop_reason = "completed"
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
    search_space: SearchSpace,
    seeds: Sequence[StrategyGenome],
    seen: set[str],
    rng: np.random.Generator,
    seed: int,
    population_size: int,
    max_lineage_depth: int,
) -> tuple[StrategyGenome, ...]:
    proposals: list[StrategyGenome] = []
    parents = tuple(archive[: min(8, len(archive))]) or tuple(current[:1])
    for parent in parents:
        neighborhood = parameter_neighborhood(parent.genome, search_space)
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
    scouts = sample_diverse_population(
        search_space,
        seed=seed + 91_337,
        count=max(scout_slots, scout_slots * 4),
    )

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
        key=lambda item: (-_distance_from_baseline(item), item.fingerprint),
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


def _distance_from_baseline(genome: StrategyGenome) -> int:
    baseline = StrategyGenome.baseline()
    blocks = (
        (
            "entry_mode",
            "entry_value",
            "entry_expiry_min",
            "entry_ladder_mode",
            "entry_ladder_step",
        ),
        ("leg_count", "volume_weights"),
        ("target_mode", "target_value", "partial_fraction", "runner_target"),
        ("be_mode", "be_trigger"),
        ("stop_mode", "stop_value"),
        ("profit_lock_arm", "profit_lock_giveback"),
        ("time_exit_min",),
        ("provider_management_mode",),
        ("context_filter_mode", "context_filter_value"),
    )
    return sum(
        any(getattr(genome, name) != getattr(baseline, name) for name in block)
        for block in blocks
    )


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
    if payload.get("fold") != asdict(fold):
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
    return json.loads(json.dumps(
        dict(context or {}),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ))
