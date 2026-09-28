"""Observed basket money shared by live guards and offline consumers."""

from dataclasses import dataclass

import dubai_live_candidate as dubai
import gold_555_live_candidate as gold


@dataclass(frozen=True)
class GuardObservation:
    floating_pl: float
    realized_pl: float
    realized_complete: bool
    total_pl: float | None
    observed_pl: float
    n_open: int


def guard_observation(summary):
    if summary.get("positions_complete", True) is False:
        raise RuntimeError("MT5 positions_get unavailable for basket guard")
    floating = float(summary.get("floating_pl", summary.get("pl")) or 0.0)
    realized = float(summary.get("realized_pl") or 0.0)
    complete = bool(summary.get("realized_complete", True))
    total = summary.get("total_pl")
    if total is None and complete:
        total = summary.get("pl", floating + realized)
    observed = float(total) if total is not None else floating
    return GuardObservation(floating, realized, complete, total, observed, int(summary.get("n_open") or 0))


def evaluate_observed_guard(*, policy, state, observation, elapsed_min):
    if not isinstance(observation, GuardObservation):
        raise TypeError("typed guard observation required")
    if isinstance(policy, dubai.DubaiLivePolicy) and isinstance(state, dubai.DubaiGuardState):
        evaluate = dubai.evaluate_guard
    elif isinstance(policy, gold.Gold555Policy) and isinstance(state, gold.Gold555GuardState):
        evaluate = gold.evaluate_guard
    else:
        raise TypeError("matching canonical guard policy and state required")
    return evaluate(policy=policy, state=state, total_pl=observation.observed_pl,
                    n_open=observation.n_open, elapsed_min=elapsed_min,
                    money_evidence_complete=observation.realized_complete)
