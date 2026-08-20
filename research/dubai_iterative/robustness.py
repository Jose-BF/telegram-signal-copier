"""Scenario-aware ranking for bounded Dubai strategy research."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Mapping, Sequence

from .contracts import StrategyGenome
from .evolution import CandidateEvaluation
from .statistics import DailyStabilityAssessment, assess_daily_stability


@dataclass(frozen=True)
class ScenarioEvaluation:
    name: str
    evaluation: CandidateEvaluation

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("scenario name cannot be empty")


@dataclass(frozen=True)
class ExecutionRobustnessAssessment:
    genome: StrategyGenome
    scenarios: tuple[ScenarioEvaluation, ...]
    worst_net_eur: Decimal | None
    best_net_eur: Decimal | None
    net_range_eur: Decimal | None
    worst_normalized_net_per_001: Decimal | None
    maximum_normalized_drawdown_per_001: Decimal | None
    maximum_drawdown_eur: Decimal | None
    minimum_participation: float
    profitable_scenarios: int
    scenario_count: int
    positive_challenges: int
    challenge_count: int
    worst_challenge_net_eur: Decimal | None
    positive_challenge_ratio: float
    worst_return_over_drawdown: Decimal | None
    risk_limit_eur: Decimal | None
    risk_eligible: bool
    evidence_complete: bool
    robustness_eligible: bool


@dataclass(frozen=True)
class ObservationalEquivalenceGroup:
    """Rules that made identical decisions on every evaluated scenario."""

    behavior_id: str
    representative: ExecutionRobustnessAssessment
    members: tuple[ExecutionRobustnessAssessment, ...]

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def member_fingerprints(self) -> tuple[str, ...]:
        return tuple(sorted(item.genome.fingerprint for item in self.members))


@dataclass(frozen=True)
class RobustDailyStabilityAssessment:
    genome: StrategyGenome
    scenarios: tuple[tuple[str, DailyStabilityAssessment], ...]
    scenario_count: int
    minimum_bootstrap_probability_positive: float | None
    worst_bootstrap_p05_eur: Decimal | None
    worst_normalized_bootstrap_p05_per_001: Decimal | None
    minimum_leave_one_day_out_positive_ratio: float | None
    maximum_positive_day_concentration: float | None
    blockers: tuple[str, ...]

    @property
    def evidence_complete(self) -> bool:
        return self.scenario_count > 0 and not self.blockers


def assess_execution_robustness(
    scenarios: Sequence[ScenarioEvaluation],
    *,
    minimum_participation: float = 0.50,
    folds: Sequence[object] = (),
    minimum_positive_challenge_ratio: float = 0.0,
    maximum_drawdown_eur: Decimal | None = None,
) -> ExecutionRobustnessAssessment:
    """Collapse fixed-world scenario results using their worst outcome."""

    scenarios = tuple(scenarios)
    folds = tuple(folds)
    if not scenarios:
        raise ValueError("at least one execution scenario is required")
    if not 0.0 <= minimum_participation <= 1.0:
        raise ValueError("minimum_participation must be between zero and one")
    if not 0.0 <= minimum_positive_challenge_ratio <= 1.0:
        raise ValueError(
            "minimum_positive_challenge_ratio must be between zero and one"
        )
    if maximum_drawdown_eur is not None and maximum_drawdown_eur <= 0:
        raise ValueError("maximum_drawdown_eur must be positive")
    names = [item.name for item in scenarios]
    if len(set(names)) != len(names):
        raise ValueError("execution scenario names must be unique")
    fingerprints = {
        item.evaluation.genome.fingerprint
        for item in scenarios
    }
    if len(fingerprints) != 1:
        raise ValueError("execution scenarios must evaluate one genome")

    evaluations = tuple(item.evaluation for item in scenarios)
    complete = all(
        not item.blockers
        and item.net_eur is not None
        and item.max_drawdown_eur is not None
        and item.normalized_net_per_001 is not None
        and item.normalized_max_drawdown_per_001 is not None
        for item in evaluations
    )
    nets = tuple(
        item.net_eur for item in evaluations if item.net_eur is not None
    )
    normalized_nets = tuple(
        item.normalized_net_per_001
        for item in evaluations
        if item.normalized_net_per_001 is not None
    )
    normalized_drawdowns = tuple(
        item.normalized_max_drawdown_per_001
        for item in evaluations
        if item.normalized_max_drawdown_per_001 is not None
    )
    drawdowns = tuple(
        item.max_drawdown_eur
        for item in evaluations
        if item.max_drawdown_eur is not None
    )
    challenge_values: list[Decimal] = []
    challenge_complete = True
    for scenario in scenarios:
        for fold in folds:
            rows = tuple(
                (day, result)
                for day, result in scenario.evaluation.results
                if fold.challenge_contains(day)
            )
            if not rows:
                challenge_complete = False
                continue
            challenged = CandidateEvaluation.from_results(
                scenario.evaluation.genome,
                rows,
            )
            if challenged.blockers or challenged.net_eur is None:
                challenge_complete = False
            else:
                challenge_values.append(challenged.net_eur)
    complete = complete and challenge_complete
    worst_net = min(nets) if complete else None
    best_net = max(nets) if complete else None
    minimum_participation_seen = min(
        (item.participation_rate for item in evaluations),
        default=0.0,
    )
    profitable = sum(item.net_eur is not None and item.net_eur > 0 for item in evaluations)
    positive_challenges = sum(value > 0 for value in challenge_values)
    challenge_count = len(scenarios) * len(folds)
    challenge_ratio = (
        positive_challenges / challenge_count
        if challenge_count
        else 1.0
    )
    maximum_drawdown = max(drawdowns) if complete else None
    risk_eligible = (
        complete
        and maximum_drawdown is not None
        and (
            maximum_drawdown_eur is None
            or maximum_drawdown <= maximum_drawdown_eur
        )
    )
    if not complete or worst_net is None or maximum_drawdown is None:
        return_over_drawdown = None
    elif maximum_drawdown == 0:
        return_over_drawdown = (
            Decimal("Infinity") if worst_net > 0 else Decimal("0")
        )
    else:
        return_over_drawdown = worst_net / maximum_drawdown
    eligible = (
        complete
        and profitable == len(evaluations)
        and minimum_participation_seen >= minimum_participation
        and challenge_ratio >= minimum_positive_challenge_ratio
        and risk_eligible
    )
    return ExecutionRobustnessAssessment(
        genome=evaluations[0].genome,
        scenarios=scenarios,
        worst_net_eur=worst_net,
        best_net_eur=best_net,
        net_range_eur=(best_net - worst_net if complete else None),
        worst_normalized_net_per_001=(
            min(normalized_nets) if complete else None
        ),
        maximum_normalized_drawdown_per_001=(
            max(normalized_drawdowns) if complete else None
        ),
        maximum_drawdown_eur=maximum_drawdown,
        minimum_participation=minimum_participation_seen,
        profitable_scenarios=profitable,
        scenario_count=len(evaluations),
        positive_challenges=positive_challenges,
        challenge_count=challenge_count,
        worst_challenge_net_eur=(
            min(challenge_values) if complete and challenge_values else None
        ),
        positive_challenge_ratio=challenge_ratio,
        worst_return_over_drawdown=return_over_drawdown,
        risk_limit_eur=maximum_drawdown_eur,
        risk_eligible=risk_eligible,
        evidence_complete=complete,
        robustness_eligible=eligible,
    )


def rank_robust_candidates(
    candidates: Sequence[ExecutionRobustnessAssessment],
) -> tuple[ExecutionRobustnessAssessment, ...]:
    """Rank rule quality before raw exposure or a single lucky scenario."""

    return tuple(sorted(candidates, key=_robust_score, reverse=True))


def group_observationally_equivalent(
    candidates: Sequence[ExecutionRobustnessAssessment],
) -> tuple[ObservationalEquivalenceGroup, ...]:
    """Collapse only rules whose complete simulated actions are identical.

    Genome fingerprints remain distinct.  This grouping is deliberately tied
    to the current evidence window: rules can separate again when future data
    makes a previously dormant condition change an action.
    """

    grouped: dict[str, list[ExecutionRobustnessAssessment]] = {}
    for candidate in candidates:
        behavior_id = _observed_behavior_id(candidate)
        grouped.setdefault(behavior_id, []).append(candidate)

    groups_by_representative: dict[str, ObservationalEquivalenceGroup] = {}
    for behavior_id, members in grouped.items():
        ordered_members = tuple(sorted(
            members,
            key=lambda item: (
                item.scenarios[0].evaluation.complexity,
                item.genome.lineage_depth,
                item.genome.fingerprint,
            ),
        ))
        representative = ordered_members[0]
        groups_by_representative[representative.genome.fingerprint] = (
            ObservationalEquivalenceGroup(
                behavior_id=behavior_id,
                representative=representative,
                members=ordered_members,
            )
        )

    ranked_representatives = rank_robust_candidates(tuple(
        group.representative for group in groups_by_representative.values()
    ))
    return tuple(
        groups_by_representative[item.genome.fingerprint]
        for item in ranked_representatives
    )


def assess_robust_daily_stability(
    candidate: ExecutionRobustnessAssessment,
    *,
    samples: int = 10_000,
    seed: int = 20260817,
) -> RobustDailyStabilityAssessment:
    """Measure dependence on particular days in every execution world."""

    scenario_rows: list[tuple[str, DailyStabilityAssessment]] = []
    blockers: list[str] = []
    for index, scenario in enumerate(
        sorted(candidate.scenarios, key=lambda item: item.name)
    ):
        assessment = assess_daily_stability(
            (
                (day, result.pnl_eur)
                for day, result in scenario.evaluation.results
            ),
            samples=samples,
            seed=seed + index,
        )
        scenario_rows.append((scenario.name, assessment))
        blockers.extend(
            f"{scenario.name}:{item}" for item in assessment.blockers
        )

    complete = bool(scenario_rows) and not blockers
    probabilities = tuple(
        item.bootstrap_probability_positive
        for _name, item in scenario_rows
        if item.bootstrap_probability_positive is not None
    )
    p05_values = tuple(
        item.bootstrap_p05_eur
        for _name, item in scenario_rows
        if item.bootstrap_p05_eur is not None
    )
    leave_one_out = tuple(
        item.leave_one_day_out_positive_ratio
        for _name, item in scenario_rows
        if item.leave_one_day_out_positive_ratio is not None
    )
    concentrations = tuple(
        item.largest_positive_day_share
        for _name, item in scenario_rows
        if item.largest_positive_day_share is not None
    )
    if not (
        complete
        and len(probabilities) == len(scenario_rows)
        and len(p05_values) == len(scenario_rows)
        and len(leave_one_out) == len(scenario_rows)
        and len(concentrations) == len(scenario_rows)
    ):
        complete = False
        if not blockers:
            blockers.append("incomplete_scenario_daily_stability")

    planned_volume = sum(
        (Decimal(str(value)) for value in candidate.genome.volume_weights),
        start=Decimal("0"),
    )
    normalization = (
        Decimal("0.01") / planned_volume if planned_volume > 0 else None
    )
    worst_p05 = min(p05_values) if complete else None
    return RobustDailyStabilityAssessment(
        genome=candidate.genome,
        scenarios=tuple(scenario_rows),
        scenario_count=len(scenario_rows),
        minimum_bootstrap_probability_positive=(
            min(probabilities) if complete else None
        ),
        worst_bootstrap_p05_eur=worst_p05,
        worst_normalized_bootstrap_p05_per_001=(
            worst_p05 * normalization
            if worst_p05 is not None and normalization is not None
            else None
        ),
        minimum_leave_one_day_out_positive_ratio=(
            min(leave_one_out) if complete else None
        ),
        maximum_positive_day_concentration=(
            max(concentrations) if complete else None
        ),
        blockers=tuple(dict.fromkeys(blockers)),
    )


def rank_observational_groups(
    groups: Sequence[ObservationalEquivalenceGroup],
    daily_stability: Mapping[str, RobustDailyStabilityAssessment],
) -> tuple[ObservationalEquivalenceGroup, ...]:
    """Rank distinct historical behavior, not dormant rule variants."""

    def score(group: ObservationalEquivalenceGroup):
        representative = group.representative
        stability = daily_stability.get(representative.genome.fingerprint)
        return (
            stability is not None and stability.evidence_complete,
            (
                stability.minimum_bootstrap_probability_positive
                if stability is not None
                and stability.minimum_bootstrap_probability_positive is not None
                else -1.0
            ),
            (
                stability.minimum_leave_one_day_out_positive_ratio
                if stability is not None
                and stability.minimum_leave_one_day_out_positive_ratio is not None
                else -1.0
            ),
            _decimal_or_low(
                stability.worst_normalized_bootstrap_p05_per_001
                if stability is not None
                else None
            ),
            -(
                stability.maximum_positive_day_concentration
                if stability is not None
                and stability.maximum_positive_day_concentration is not None
                else float("inf")
            ),
            *_robust_score(representative),
        )

    return tuple(sorted(groups, key=score, reverse=True))


def _observed_behavior_id(
    candidate: ExecutionRobustnessAssessment,
) -> str:
    scenarios = []
    for scenario in sorted(candidate.scenarios, key=lambda item: item.name):
        results = []
        for day, result in sorted(
            scenario.evaluation.results,
            key=lambda row: (str(row[0]), row[1].signal_id),
        ):
            entries = sorted((
                {
                    "tick_index": item.tick_index,
                    "opened_at": item.opened_at.isoformat(),
                    "entry_price": float(item.entry_price).hex(),
                    "volume": float(item.volume).hex(),
                }
                for item in result.entries
            ), key=_canonical_sort_key)
            exits = sorted((
                {
                    "tick_index": item.tick_index,
                    "closed_at": item.closed_at.isoformat(),
                    "entry_price": float(item.entry_price).hex(),
                    "exit_price": float(item.exit_price).hex(),
                    "volume": float(item.volume).hex(),
                    "pnl_eur": _canonical_decimal(item.pnl_eur),
                    "reason": item.reason,
                }
                for item in result.exits
            ), key=_canonical_sort_key)
            results.append({
                "day": str(day),
                "signal_id": result.signal_id,
                "entries": entries,
                "exits": exits,
                "pnl_eur": _canonical_decimal(result.pnl_eur),
                "exit_reason": result.exit_reason,
                "max_favourable_eur": _canonical_decimal(
                    result.max_favourable_eur
                ),
                "max_adverse_eur": _canonical_decimal(result.max_adverse_eur),
                "max_floating_drawdown_eur": _canonical_decimal(
                    result.max_floating_drawdown_eur
                ),
                "max_favourable_move": float(result.max_favourable_move).hex(),
                "max_adverse_move": float(result.max_adverse_move).hex(),
                "blockers": sorted(result.blockers),
                "last_tick_index": result.last_tick_index,
                "unfilled": result.unfilled,
                "filled_volume": float(result.filled_volume).hex(),
            })
        scenarios.append({"name": scenario.name, "results": results})
    encoded = json.dumps(
        scenarios,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_sort_key(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _canonical_decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _robust_score(item: ExecutionRobustnessAssessment):
    scenario_ratio = (
        item.profitable_scenarios / item.scenario_count
        if item.scenario_count
        else 0.0
    )
    return (
        item.robustness_eligible,
        item.evidence_complete,
        scenario_ratio,
        item.positive_challenge_ratio,
        _decimal_or_low(item.worst_return_over_drawdown),
        _decimal_or_low(item.worst_normalized_net_per_001),
        -_decimal_or_high(item.maximum_normalized_drawdown_per_001),
        item.minimum_participation,
        -_decimal_or_high(item.net_range_eur),
        -item.genome.lineage_depth,
        item.genome.fingerprint,
    )


def _decimal_or_low(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal("-Infinity")


def _decimal_or_high(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal("Infinity")
