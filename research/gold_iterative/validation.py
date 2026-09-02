"""Fail-closed stability selection for Gold Signals research candidates."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.robustness import (
    ExecutionRobustnessAssessment,
    ObservationalEquivalenceGroup,
    RobustDailyStabilityAssessment,
    assess_robust_daily_stability,
    group_observationally_equivalent,
    rank_observational_groups,
)


@dataclass(frozen=True)
class GoldStabilityPolicy:
    bootstrap_samples: int = 10_000
    seed: int = 20260902
    minimum_bootstrap_probability_positive: float = 0.95
    minimum_leave_one_day_out_positive_ratio: float = 1.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.bootstrap_samples, bool)
            or not isinstance(self.bootstrap_samples, int)
            or self.bootstrap_samples <= 0
        ):
            raise ValueError("bootstrap_samples must be a positive integer")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        for name in (
            "minimum_bootstrap_probability_positive",
            "minimum_leave_one_day_out_positive_ratio",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a probability")


@dataclass(frozen=True)
class GoldValidatedCandidate:
    group: ObservationalEquivalenceGroup
    stability: RobustDailyStabilityAssessment
    blockers: tuple[str, ...]

    @property
    def genome(self) -> StrategyGenome:
        return self.group.representative.genome

    @property
    def assessment(self) -> ExecutionRobustnessAssessment:
        return self.group.representative

    @property
    def eligible(self) -> bool:
        return not self.blockers


@dataclass(frozen=True)
class GoldCandidateValidation:
    candidates: tuple[GoldValidatedCandidate, ...]
    eligible: tuple[GoldValidatedCandidate, ...]
    rejected: tuple[GoldValidatedCandidate, ...]


def validate_gold_candidates(
    candidates: Sequence[ExecutionRobustnessAssessment],
    *,
    policy: GoldStabilityPolicy | None = None,
) -> GoldCandidateValidation:
    """Collapse duplicate behavior and reject profit dependent on lucky days."""

    active_policy = policy or GoldStabilityPolicy()
    groups = group_observationally_equivalent(tuple(candidates))
    stability_by_fingerprint = {
        group.representative.genome.fingerprint: assess_robust_daily_stability(
            group.representative,
            samples=active_policy.bootstrap_samples,
            seed=active_policy.seed,
        )
        for group in groups
    }
    ranked = rank_observational_groups(groups, stability_by_fingerprint)
    rows = tuple(
        _validated_candidate(
            group,
            stability_by_fingerprint[group.representative.genome.fingerprint],
            active_policy,
        )
        for group in ranked
    )
    return GoldCandidateValidation(
        candidates=rows,
        eligible=tuple(item for item in rows if item.eligible),
        rejected=tuple(item for item in rows if not item.eligible),
    )


def _validated_candidate(
    group: ObservationalEquivalenceGroup,
    stability: RobustDailyStabilityAssessment,
    policy: GoldStabilityPolicy,
) -> GoldValidatedCandidate:
    blockers: list[str] = []
    assessment = group.representative
    if not assessment.robustness_eligible:
        blockers.append("execution_robustness_failed")
    if not stability.evidence_complete:
        blockers.append("daily_stability_incomplete")
        blockers.extend(stability.blockers)
    else:
        probability = stability.minimum_bootstrap_probability_positive
        if (
            probability is None
            or probability
            < policy.minimum_bootstrap_probability_positive
        ):
            blockers.append("bootstrap_probability_below_threshold")
        p05 = stability.worst_bootstrap_p05_eur
        if p05 is None or p05 <= Decimal("0"):
            blockers.append("bootstrap_p05_not_positive")
        leave_one_out = stability.minimum_leave_one_day_out_positive_ratio
        if (
            leave_one_out is None
            or leave_one_out
            < policy.minimum_leave_one_day_out_positive_ratio
        ):
            blockers.append("leave_one_day_out_not_robust")
    return GoldValidatedCandidate(
        group=group,
        stability=stability,
        blockers=tuple(dict.fromkeys(blockers)),
    )
