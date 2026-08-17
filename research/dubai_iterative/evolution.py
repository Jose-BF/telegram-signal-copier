"""Structured critique and bounded variation for Dubai strategies."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import math
import random
from typing import Iterable, Protocol, Sequence

from .contracts import SearchSpace, StrategyGenome
from .engine import SimulationResult


@dataclass(frozen=True)
class DiagnosticEvidence:
    label: str
    signal_id: str
    observed: Decimal | str
    comparison: Decimal | str


@dataclass(frozen=True)
class Diagnosis:
    labels: tuple[str, ...]
    evidence: tuple[DiagnosticEvidence, ...]


@dataclass(frozen=True)
class CandidateEvaluation:
    genome: StrategyGenome
    results: tuple[tuple[str, SimulationResult], ...]
    net_eur: Decimal | None
    max_drawdown_eur: Decimal | None
    worst_day_eur: Decimal | None
    gross_profit_eur: Decimal | None
    gross_loss_eur: Decimal | None
    profit_factor: float | None
    positive_day_concentration: float | None
    normalized_net_per_001: Decimal | None
    max_signal_exposure: float
    complexity: int
    blockers: tuple[str, ...]

    @classmethod
    def from_results(
        cls,
        genome: StrategyGenome,
        rows: Iterable[tuple[str, SimulationResult]],
    ) -> "CandidateEvaluation":
        results = tuple(rows)
        blockers = tuple(dict.fromkeys(
            blocker
            for _day, result in results
            for blocker in result.blockers
        ))
        money_complete = all(result.pnl_eur is not None for _day, result in results)
        values = [
            result.pnl_eur
            for _day, result in results
            if result.pnl_eur is not None
        ]
        net = sum(values, start=Decimal("0")) if money_complete else None
        gross_profit = (
            sum((value for value in values if value > 0), start=Decimal("0"))
            if money_complete
            else None
        )
        gross_loss = (
            sum((-value for value in values if value < 0), start=Decimal("0"))
            if money_complete
            else None
        )
        if gross_profit is None or gross_loss is None:
            profit_factor = None
        elif gross_loss == 0:
            profit_factor = math.inf if gross_profit > 0 else 0.0
        else:
            profit_factor = float(gross_profit / gross_loss)

        realized_drawdown = Decimal("0")
        equity = Decimal("0")
        peak = Decimal("0")
        day_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        floating_drawdown = Decimal("0")
        total_volume = Decimal("0")
        max_exposure = 0.0
        for day, result in results:
            max_exposure = max(max_exposure, result.filled_volume)
            total_volume += Decimal(str(result.filled_volume))
            if result.max_floating_drawdown_eur is not None:
                floating_drawdown = max(
                    floating_drawdown,
                    result.max_floating_drawdown_eur,
                )
            if result.pnl_eur is None:
                continue
            equity += result.pnl_eur
            peak = max(peak, equity)
            realized_drawdown = max(realized_drawdown, peak - equity)
            day_totals[day] += result.pnl_eur
        max_drawdown = (
            max(realized_drawdown, floating_drawdown) if money_complete else None
        )
        worst_day = (
            min(day_totals.values(), default=Decimal("0"))
            if money_complete
            else None
        )
        positive_days = [value for value in day_totals.values() if value > 0]
        total_positive_days = sum(positive_days, start=Decimal("0"))
        concentration = (
            float(max(positive_days) / total_positive_days)
            if total_positive_days > 0
            else 0.0
        ) if money_complete else None
        normalized = None
        if net is not None and total_volume > 0:
            normalized = (net / total_volume * Decimal("0.01")).quantize(
                Decimal("0.01")
            )
        return cls(
            genome=genome,
            results=results,
            net_eur=net,
            max_drawdown_eur=max_drawdown,
            worst_day_eur=worst_day,
            gross_profit_eur=gross_profit,
            gross_loss_eur=gross_loss,
            profit_factor=profit_factor,
            positive_day_concentration=concentration,
            normalized_net_per_001=normalized,
            max_signal_exposure=max_exposure,
            complexity=_genome_complexity(genome),
            blockers=blockers,
        )


@dataclass(frozen=True)
class EvolutionBatch:
    children: tuple[StrategyGenome, ...]
    diagnoses: tuple[tuple[str, Diagnosis], ...]


class Critic(Protocol):
    def diagnose(self, evaluation: CandidateEvaluation) -> Diagnosis: ...


def diagnose(evaluation: CandidateEvaluation) -> Diagnosis:
    labels: list[str] = []
    evidence: list[DiagnosticEvidence] = []
    profitable_days: set[str] = set()
    losing_days: set[str] = set()
    for day, result in evaluation.results:
        if result.blockers or result.pnl_eur is None:
            labels.append("evidence_integrity_failure")
            evidence.append(DiagnosticEvidence(
                "evidence_integrity_failure",
                result.signal_id,
                ",".join(result.blockers),
                "complete_money_path",
            ))
            continue
        pnl = result.pnl_eur
        favourable = result.max_favourable_eur or Decimal("0")
        adverse = result.max_adverse_eur or Decimal("0")
        if pnl > 0:
            profitable_days.add(day)
        elif pnl < 0:
            losing_days.add(day)
        giveback = favourable - pnl
        if favourable >= Decimal("3") and giveback >= max(
            Decimal("2"), favourable * Decimal("0.40")
        ):
            labels.append("profit_given_back")
            evidence.append(DiagnosticEvidence(
                "profit_given_back",
                result.signal_id,
                giveback,
                favourable,
            ))
        if pnl <= Decimal("-10"):
            labels.extend(("large_loss", "reduce_exposure"))
            evidence.append(DiagnosticEvidence(
                "large_loss",
                result.signal_id,
                pnl,
                Decimal("-10"),
            ))
        if result.exit_reason == "break_even" and favourable >= Decimal("3"):
            labels.append("harmful_be")
            evidence.append(DiagnosticEvidence(
                "harmful_be",
                result.signal_id,
                favourable,
                pnl,
            ))
        elif (
            result.exit_reason == "break_even"
            and pnl >= Decimal("-1")
            and adverse <= Decimal("-3")
        ):
            labels.append("helpful_be")
            evidence.append(DiagnosticEvidence(
                "helpful_be",
                result.signal_id,
                pnl,
                adverse,
            ))
        if result.exit_reason in {"time_exit", "data_end"} and abs(pnl) <= 2:
            labels.append("stagnation")
        if result.exit_reason == "provider_close" and pnl < 0:
            labels.append("provider_management_harmful")
        elif result.exit_reason == "provider_close" and pnl > 0:
            labels.append("provider_management_helpful")
        if adverse <= Decimal("-10") and pnl > 0:
            labels.append("deep_recovery")
        marginal = _marginal_last_leg(result)
        if marginal is not None:
            labels.append("marginal_leg_damage")
            evidence.append(DiagnosticEvidence(
                "marginal_leg_damage",
                result.signal_id,
                marginal,
                pnl - marginal,
            ))

    if (
        evaluation.net_eur is not None
        and evaluation.net_eur > 0
        and len(profitable_days) >= 2
        and not losing_days
        and (
            evaluation.max_drawdown_eur is None
            or evaluation.max_drawdown_eur <= evaluation.net_eur * Decimal("0.50")
        )
    ):
        labels.append("stable_positive_exposure")
    if (
        evaluation.positive_day_concentration is not None
        and evaluation.positive_day_concentration > 0.65
    ):
        labels.append("day_concentration")
    if not labels:
        labels.append("no_dominant_failure")
    return Diagnosis(
        labels=tuple(dict.fromkeys(labels)),
        evidence=tuple(evidence),
    )


def diagnose_against_reference(
    evaluation: CandidateEvaluation,
    reference: CandidateEvaluation,
    *,
    material_difference: Decimal = Decimal("2.00"),
) -> Diagnosis:
    """Add only comparisons available from a separately simulated strategy."""

    base = diagnose(evaluation)
    labels = list(base.labels)
    if labels == ["no_dominant_failure"]:
        labels.clear()
    evidence = list(base.evidence)
    reference_by_signal = {
        result.signal_id: result
        for _day, result in reference.results
    }
    for _day, result in evaluation.results:
        other = reference_by_signal.get(result.signal_id)
        if (
            other is None
            or result.blockers
            or other.blockers
            or result.pnl_eur is None
            or other.pnl_eur is None
        ):
            continue
        improvement = other.pnl_eur - result.pnl_eur
        if improvement < material_difference:
            continue
        if result.exit_reason in {"basket_stop", "fixed_sl", "provider_sl"} and other.pnl_eur > 0:
            labels.append("stop_before_recovery")
            evidence.append(DiagnosticEvidence(
                "stop_before_recovery",
                result.signal_id,
                result.pnl_eur,
                other.pnl_eur,
            ))
        if result.exit_reason in {
            "basket_target",
            "provider_target_all",
            "provider_tp",
            "runner_target",
        }:
            labels.append("premature_target")
            evidence.append(DiagnosticEvidence(
                "premature_target",
                result.signal_id,
                result.pnl_eur,
                other.pnl_eur,
            ))
        if evaluation.genome.entry_mode != reference.genome.entry_mode:
            labels.append("entry_timing_cost")
            evidence.append(DiagnosticEvidence(
                "entry_timing_cost",
                result.signal_id,
                result.pnl_eur,
                other.pnl_eur,
            ))
        if result.exit_reason == "break_even":
            labels.append("harmful_be")
        if result.exit_reason == "provider_close":
            labels.append("provider_management_harmful")
    if not labels:
        labels.append("no_dominant_failure")
    return Diagnosis(
        labels=tuple(dict.fromkeys(labels)),
        evidence=tuple(evidence),
    )


def mutate_from_diagnosis(
    parent: StrategyGenome,
    diagnosis: Diagnosis,
    *,
    search_space: SearchSpace,
    seed: int,
) -> tuple[StrategyGenome, ...]:
    rng = random.Random(seed)
    proposals: list[StrategyGenome] = []

    def propose(reason: str, **changes) -> None:
        try:
            child = parent.with_change(**changes).with_lineage(
                parent_fingerprints=(parent.fingerprint,),
                mutation_reason=reason,
                lineage_depth=parent.lineage_depth + 1,
            )
        except (TypeError, ValueError):
            return
        if child.validation_errors() or search_space.validation_errors(child):
            return
        proposals.append(child)

    labels = set(diagnosis.labels)
    if "profit_given_back" in labels:
        favourable = [
            float(item.comparison)
            for item in diagnosis.evidence
            if item.label == "profit_given_back"
            and isinstance(item.comparison, Decimal)
        ]
        anchor = max(3.0, _median(favourable) if favourable else 8.0)
        for arm_ratio, giveback_ratio in ((0.4, 0.25), (0.6, 0.3), (0.8, 0.4)):
            propose(
                "profit_given_back",
                profit_lock_arm=round(anchor * arm_ratio, 2),
                profit_lock_giveback=round(max(1.0, anchor * giveback_ratio), 2),
            )
        propose(
            "profit_given_back",
            target_mode="fixed_basket",
            target_value=round(max(2.0, anchor * 0.6), 2),
        )
    if "large_loss" in labels:
        for limit in (5.0, 10.0, 15.0, 20.0):
            propose("large_loss", stop_mode="basket_money", stop_value=limit)
    if "stop_before_recovery" in labels:
        if parent.stop_mode in {"basket_money", "fixed_move"}:
            current = float(parent.stop_value)
            for multiplier in (1.5, 2.0):
                propose(
                    "stop_before_recovery",
                    stop_mode=parent.stop_mode,
                    stop_value=round(current * multiplier, 2),
                )
        propose("stop_before_recovery", stop_mode="none", stop_value=None)
    if "reduce_exposure" in labels or "day_concentration" in labels:
        reduced = _reduce_exposure(parent, search_space)
        if reduced is not None:
            propose("reduce_exposure", **reduced)
    if "stable_positive_exposure" in labels:
        for multiplier in (1.5, 2.0, 3.0):
            increased = _scale_exposure(parent, search_space, multiplier)
            if increased is not None:
                propose("stable_positive_exposure", **increased)
    if "harmful_be" in labels:
        propose("harmful_be", be_mode="none", be_trigger=None)
        propose("harmful_be", be_mode="delayed", be_trigger=10.0)
    if "helpful_be" in labels:
        propose("helpful_be", be_mode="provider", be_trigger=None)
        propose("helpful_be", be_mode="price", be_trigger=1.0)
    if "premature_target" in labels:
        if parent.target_mode == "fixed_basket":
            current = float(parent.target_value)
            for multiplier in (1.5, 2.0, 3.0):
                propose(
                    "premature_target",
                    target_mode="fixed_basket",
                    target_value=round(current * multiplier, 2),
                )
            propose(
                "premature_target",
                target_mode="partial_runner",
                target_value=current,
                partial_fraction=0.5,
                runner_target=round(current * 3.0, 2),
            )
        propose(
            "premature_target",
            target_mode="none",
            target_value=None,
            partial_fraction=0.0,
            runner_target=None,
        )
    if "stagnation" in labels:
        for minutes in (5, 10, 20, 30, 60):
            if minutes != parent.time_exit_min:
                propose("stagnation", time_exit_min=minutes)
    if "provider_management_harmful" in labels:
        propose("provider_management_harmful", provider_management_mode="ignore")
        propose("provider_management_harmful", provider_management_mode="close_only")
    if "provider_management_helpful" in labels:
        propose("provider_management_helpful", provider_management_mode="exact")
    if "deep_recovery" in labels:
        propose("deep_recovery", stop_mode="none", stop_value=None)
        propose("deep_recovery", entry_mode="pullback", entry_value=1.0)
    if "entry_timing_cost" in labels:
        propose("entry_timing_cost", entry_mode="actual_mt5", entry_value=None)
        if parent.entry_value is not None:
            for multiplier in (0.5, 1.5):
                propose(
                    "entry_timing_cost",
                    entry_value=round(float(parent.entry_value) * multiplier, 3),
                )
        for expiry in (5, 15, 30, 60):
            propose("entry_timing_cost", entry_expiry_min=expiry)
    if "marginal_leg_damage" in labels:
        reduced = _reduce_exposure(parent, search_space)
        if reduced is not None:
            propose("marginal_leg_damage", **reduced)
    if "no_dominant_failure" in labels:
        novelty = rng.choice(("delay", "pullback", "momentum"))
        propose("novel_entry_probe", entry_mode=novelty, entry_value=1.0)

    return deduplicate(proposals)


def crossover(
    left: StrategyGenome,
    right: StrategyGenome,
    *,
    search_space: SearchSpace,
    seed: int,
) -> tuple[StrategyGenome, ...]:
    """Exchange coherent rule blocks without mixing dependent fields."""

    if left.fingerprint == right.fingerprint:
        return ()
    blocks = (
        ("entry_mode", "entry_value", "entry_expiry_min"),
        ("leg_count", "volume_weights"),
        ("target_mode", "target_value", "partial_fraction", "runner_target"),
        (
            "be_mode",
            "be_trigger",
            "stop_mode",
            "stop_value",
            "profit_lock_arm",
            "profit_lock_giveback",
            "time_exit_min",
            "provider_management_mode",
        ),
        ("context_filter_mode", "context_filter_value"),
    )
    proposals: list[StrategyGenome] = []
    depth = max(left.lineage_depth, right.lineage_depth) + 1
    for destination, donor in ((left, right), (right, left)):
        for block in blocks:
            changes = {field: getattr(donor, field) for field in block}
            child = destination.with_change(**changes).with_lineage(
                parent_fingerprints=(left.fingerprint, right.fingerprint),
                mutation_reason="compatible_block_crossover",
                lineage_depth=depth,
            )
            if child.fingerprint in {left.fingerprint, right.fingerprint}:
                continue
            if child.validation_errors() or search_space.validation_errors(child):
                continue
            proposals.append(child)
    random.Random(seed).shuffle(proposals)
    return deduplicate(proposals)


def evolve_generation(
    training_results: Sequence[CandidateEvaluation],
    *,
    critic: Critic | None = None,
    challenge_results: Sequence[CandidateEvaluation] = (),
    search_space: SearchSpace,
    seed: int,
) -> EvolutionBatch:
    # Challenge rows are intentionally accepted only so callers can keep one
    # interface. They never enter the critic or mutation path for this fold.
    _ = challenge_results
    children: list[StrategyGenome] = []
    diagnoses: list[tuple[str, Diagnosis]] = []
    for offset, evaluation in enumerate(training_results):
        current = (
            critic.diagnose(evaluation)
            if critic is not None
            else diagnose(evaluation)
        )
        diagnoses.append((evaluation.genome.fingerprint, current))
        children.extend(mutate_from_diagnosis(
            evaluation.genome,
            current,
            search_space=search_space,
            seed=seed + offset,
        ))
    return EvolutionBatch(
        children=deduplicate(children),
        diagnoses=tuple(diagnoses),
    )


def seed_population(
    search_space: SearchSpace,
    *,
    seed: int,
) -> tuple[StrategyGenome, ...]:
    rng = random.Random(seed)
    population: list[StrategyGenome] = []

    def add(genome: StrategyGenome) -> None:
        if not genome.validation_errors() and not search_space.validation_errors(genome):
            population.append(genome)

    volume_plans = _volume_plans(search_space)
    for weights in volume_plans:
        add(StrategyGenome.baseline().with_change(
            leg_count=len(weights),
            volume_weights=weights,
        ))

    base_weights = min(
        volume_plans,
        key=lambda weights: abs(sum(weights) - 0.04),
    )
    base = StrategyGenome.baseline().with_change(
        leg_count=len(base_weights),
        volume_weights=base_weights,
    )
    for mode, values in {
        "delay": (0.25, 1.0, 5.0, 15.0, 30.0),
        "pullback": (0.25, 0.5, 1.0, 2.0, 4.0),
        "momentum": (0.25, 0.5, 1.0, 2.0, 4.0),
    }.items():
        for value in values:
            add(base.with_change(entry_mode=mode, entry_value=value))
    for expiry in (1, 3, 5, 10, 15, 30, 60, 120):
        add(base.with_change(entry_expiry_min=expiry))
    for target_index in (1.0, 2.0, 3.0, 4.0, 5.0):
        add(base.with_change(
            target_mode="provider_target_all",
            target_value=target_index,
        ))
    for target in (2.0, 5.0, 10.0, 15.0, 20.0, 30.0):
        add(base.with_change(target_mode="fixed_basket", target_value=target))
    for target, runner in ((2.0, 6.0), (5.0, 12.0), (10.0, 25.0)):
        for fraction in (0.25, 0.5, 0.75):
            add(base.with_change(
                target_mode="partial_runner",
                target_value=target,
                partial_fraction=fraction,
                runner_target=runner,
            ))
    for be_trigger in (0.5, 1.0, 2.0, 4.0, 8.0):
        add(base.with_change(be_mode="price", be_trigger=be_trigger))
        add(base.with_change(be_mode="partial", be_trigger=be_trigger))
    for delay in (1.0, 3.0, 5.0, 10.0, 20.0, 30.0):
        add(base.with_change(be_mode="delayed", be_trigger=delay))
    add(base.with_change(be_mode="none"))
    add(base.with_change(
        target_mode="none",
        target_value=None,
        partial_fraction=0.0,
        runner_target=None,
    ))
    for stop in (3.0, 5.0, 10.0, 15.0, 20.0, 30.0):
        add(base.with_change(stop_mode="basket_money", stop_value=stop))
    for move in (1.0, 2.0, 4.0, 6.0, 10.0):
        add(base.with_change(stop_mode="fixed_move", stop_value=move))
    add(base.with_change(stop_mode="none"))
    for minutes in (3, 5, 10, 20, 30, 45, 60, 90, 120, 240):
        add(base.with_change(time_exit_min=minutes))
    for arm, giveback in (
        (3.0, 1.0),
        (5.0, 2.0),
        (10.0, 3.0),
        (15.0, 5.0),
        (20.0, 8.0),
    ):
        add(base.with_change(
            profit_lock_arm=arm,
            profit_lock_giveback=giveback,
        ))
    for mode in ("exact", "close_only", "ignore"):
        add(base.with_change(provider_management_mode=mode))
    for spread in (0.15, 0.20, 0.30, 0.50, 0.80):
        add(base.with_change(
            context_filter_mode="max_spread",
            context_filter_value=spread,
        ))
    for hour in (8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 22.0):
        add(base.with_change(
            context_filter_mode="time_window",
            context_filter_value=hour,
        ))
    for move in (0.5, 1.0, 2.0, 4.0, 8.0, 12.0):
        add(base.with_change(
            context_filter_mode="max_volatility",
            context_filter_value=move,
        ))
    for ratio in (0.25, 0.5, 1.0, 1.5, 2.0, 3.0):
        add(base.with_change(
            context_filter_mode="min_reward_risk",
            context_filter_value=ratio,
        ))
    rng.shuffle(population)
    return deduplicate(population)


def deduplicate(genomes: Iterable[StrategyGenome]) -> tuple[StrategyGenome, ...]:
    unique: dict[str, StrategyGenome] = {}
    for genome in genomes:
        unique.setdefault(genome.fingerprint, genome)
    return tuple(unique.values())


def pareto_front(
    evaluations: Sequence[CandidateEvaluation],
) -> tuple[CandidateEvaluation, ...]:
    eligible = [
        item
        for item in evaluations
        if item.net_eur is not None
        and item.max_drawdown_eur is not None
        and item.worst_day_eur is not None
        and not item.blockers
    ]
    frontier: list[CandidateEvaluation] = []
    for candidate in eligible:
        if any(
            other is not candidate and _dominates(other, candidate)
            for other in eligible
        ):
            continue
        frontier.append(candidate)
    return tuple(sorted(
        frontier,
        key=lambda item: (
            -(item.net_eur or Decimal("0")),
            item.max_drawdown_eur or Decimal("Infinity"),
            item.complexity,
            item.genome.fingerprint,
        ),
    ))


def _dominates(left: CandidateEvaluation, right: CandidateEvaluation) -> bool:
    left_values = (
        left.net_eur,
        -(left.max_drawdown_eur or Decimal("Infinity")),
        left.worst_day_eur,
        Decimal(str(-(left.positive_day_concentration or 0.0))),
        Decimal(-left.complexity),
    )
    right_values = (
        right.net_eur,
        -(right.max_drawdown_eur or Decimal("Infinity")),
        right.worst_day_eur,
        Decimal(str(-(right.positive_day_concentration or 0.0))),
        Decimal(-right.complexity),
    )
    return all(a >= b for a, b in zip(left_values, right_values, strict=True)) and any(
        a > b for a, b in zip(left_values, right_values, strict=True)
    )


def _volume_plans(search_space: SearchSpace) -> tuple[tuple[float, ...], ...]:
    step = search_space.volume_step
    min_steps = max(1, math.ceil(search_space.min_total_volume / step - 1e-9))
    max_steps = max(min_steps, math.floor(search_space.max_total_volume / step + 1e-9))
    reference_steps = {
        min_steps,
        max_steps,
        *(
            max(min_steps, min(max_steps, round(value / step)))
            for value in (0.02, 0.04, 0.08, 0.12, 0.16)
        ),
    }
    plans: list[tuple[float, ...]] = []
    for total_steps in sorted(reference_steps):
        for legs in (1, 2, 3, 4, 6, 8, search_space.max_legs):
            legs = min(legs, search_space.max_legs, total_steps)
            if legs <= 0:
                continue
            base, remainder = divmod(total_steps, legs)
            counts = [base + (1 if index < remainder else 0) for index in range(legs)]
            if min(counts) <= 0:
                continue
            plans.append(tuple(round(count * step, 10) for count in counts))
    return tuple(dict.fromkeys(plans))


def _reduce_exposure(
    genome: StrategyGenome,
    search_space: SearchSpace,
) -> dict | None:
    if genome.leg_count > 1:
        return {
            "leg_count": genome.leg_count - 1,
            "volume_weights": genome.volume_weights[:-1],
        }
    return _scale_exposure(genome, search_space, 0.5)


def _scale_exposure(
    genome: StrategyGenome,
    search_space: SearchSpace,
    multiplier: float,
) -> dict | None:
    step = search_space.volume_step
    weights = tuple(
        round(max(step, round(value * multiplier / step) * step), 10)
        for value in genome.volume_weights
    )
    total = sum(weights)
    if not (
        search_space.min_total_volume - 1e-12
        <= total
        <= search_space.max_total_volume + 1e-12
    ):
        return None
    if weights == genome.volume_weights:
        return None
    return {"volume_weights": weights}


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _genome_complexity(genome: StrategyGenome) -> int:
    baseline = StrategyGenome.baseline().to_dict()
    current = genome.to_dict()
    ignored = {"parent_fingerprints", "mutation_reason", "lineage_depth"}
    return sum(
        1
        for key, value in current.items()
        if key not in ignored and value != baseline.get(key)
    )


def _marginal_last_leg(result: SimulationResult) -> Decimal | None:
    if len(result.entries) < 2 or not result.exits:
        return None
    last_ticket = result.entries[-1].ticket
    values = [
        item.pnl_eur
        for item in result.exits
        if item.ticket == last_ticket and item.pnl_eur is not None
    ]
    if not values:
        return None
    marginal = sum(values, start=Decimal("0"))
    return marginal if marginal < Decimal("-0.01") else None
