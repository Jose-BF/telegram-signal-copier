"""Explicit canonical guard component, not admission of a complete strategy."""

from dataclasses import asdict, dataclass

import dubai_live_candidate as dubai
import gold_555_live_candidate as gold
from basket_management import evaluate_observed_guard, guard_observation


@dataclass(frozen=True)
class CanonicalGuardComponent:
    strategy_id: str

    def __post_init__(self):
        if self.strategy_id not in {dubai.CANDIDATE_ID, gold.CANDIDATE_ID}:
            raise ValueError("unsupported canonical guard component")

    @property
    def channel(self):
        return "canal1" if self.strategy_id == dubai.CANDIDATE_ID else "canal2"

    @property
    def fingerprint(self):
        return dubai.CANDIDATE_FINGERPRINT if self.channel == "canal1" else gold.CANDIDATE_FINGERPRINT

    def policy(self):
        return dubai.DubaiLivePolicy() if self.channel == "canal1" else gold.Gold555Policy()

    def initial_state(self):
        return dubai.DubaiGuardState() if self.channel == "canal1" else gold.Gold555GuardState()

    def evaluate(self, state, summary, elapsed_min):
        return evaluate_observed_guard(policy=self.policy(), state=state,
            observation=guard_observation(summary), elapsed_min=elapsed_min)

    def descriptor(self):
        return {"role": "canonical_guard_component_on_modelled_execution",
                "strategy_id": self.strategy_id, "strategy_fingerprint": self.fingerprint,
                "policy": asdict(self.policy()), "currency": "EUR",
                "complete_strategy_admitted": False}
