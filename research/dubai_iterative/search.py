"""Finite, resumable and chronologically isolated strategy search."""

from __future__ import annotations

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
    seed_population,
)


CHECKPOINT_SCHEMA_VERSION = 1


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

    @property
    def frontier_fingerprints(self) -> tuple[str, ...]:
        return tuple(item.genome.fingerprint for item in self.frontier)


@dataclass(frozen=True)
class ChronologicalSearchReport:
    fold_reports: tuple[SearchReport, ...]

    @property
    def total_evaluations(self) -> int:
        return sum(item.evaluations for item in self.fold_reports)


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
) -> SearchReport:
    """Run one development fold; challenge rows remain invisible until stop."""

    if population_size <= 0:
        raise ValueError("population_size must be positive")
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

    if resume_from is not None:
        state = _load_checkpoint(
            Path(resume_from),
            dataset=dataset,
            fold=fold,
            search_space=search_space,
            seed=seed,
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
        )
        rng.bit_generator.state = state["numpy_random_state"]
    else:
        seeds = seed_population(search_space, seed=seed)
        population = tuple(seeds[:population_size])
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
            )
            evaluations += len(current)
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
                generations_completed=generation_completed,
                evaluations=evaluations,
                stale_generations=stale_generations,
                elapsed_seconds=elapsed,
                seen=seen,
                next_population=next_population,
                archive=archive,
                rng=rng,
                stop_reason=stop_reason,
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
        generations_completed=generation_completed,
        evaluations=evaluations,
        stale_generations=stale_generations,
        elapsed_seconds=elapsed,
        seen=seen,
        next_population=next_population,
        archive=archive,
        rng=rng,
        stop_reason=stop_reason,
    )

    challenge = _evaluate_population(
        tuple(item.genome for item in archive),
        challenge_paths,
        evaluator,
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
    )


def _evaluate_population(
    genomes: Sequence[StrategyGenome],
    paths: Sequence[object],
    evaluator: Evaluator,
) -> tuple[CandidateEvaluation, ...]:
    return tuple(
        CandidateEvaluation.from_results(
            genome,
            (
                (str(path.day), evaluator(path, genome))
                for path in paths
            ),
        )
        for genome in genomes
    )


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

    accepted: list[StrategyGenome] = []
    for genome in deduplicate(proposals):
        if len(accepted) >= population_size:
            break
        if genome.fingerprint in seen:
            continue
        if genome.lineage_depth > max_lineage_depth:
            continue
        if genome.validation_errors() or search_space.validation_errors(genome):
            continue
        accepted.append(genome)
        seen.add(genome.fingerprint)
    return tuple(accepted)


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
        item.net_eur,
        item.max_drawdown_eur,
        item.worst_day_eur,
        None if item.positive_day_concentration is None else round(item.positive_day_concentration, 6),
        item.complexity,
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
    generations_completed: int,
    evaluations: int,
    stale_generations: int,
    elapsed_seconds: float,
    seen: set[str],
    next_population: Sequence[StrategyGenome],
    archive: Sequence[CandidateEvaluation],
    rng: np.random.Generator,
    stop_reason: str | None,
) -> None:
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "source_hashes": dict(sorted(dataset.source_hashes.items())),
        "fold": asdict(fold),
        "search_space": asdict(search_space),
        "seed": seed,
        "generations_completed": generations_completed,
        "evaluations": evaluations,
        "stale_generations": stale_generations,
        "elapsed_seconds": elapsed_seconds,
        "seen_fingerprints": sorted(seen),
        "next_population": [item.to_dict() for item in next_population],
        "archive_genomes": [item.genome.to_dict() for item in archive],
        "numpy_random_state": rng.bit_generator.state,
        "stop_reason": stop_reason,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_checkpoint(
    path: Path,
    *,
    dataset: SearchDataset,
    fold: ChronologicalFold,
    search_space: SearchSpace,
    seed: int,
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
    return payload
