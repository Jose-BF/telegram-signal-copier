"""Dubai stop planning on explicit modelled broker valuation and execution."""

from dataclasses import asdict, dataclass

import dubai_live_candidate as dubai
from research.management_observation import ReadCycleProfile


@dataclass(frozen=True)
class DubaiBasketStopComponent:
    reads: ReadCycleProfile
    valuation: str = "linear_contract_fx_unrounded_v1"
    interval_ns: int = 5_000_000_000

    def __post_init__(self):
        if not isinstance(self.reads, ReadCycleProfile):
            raise ValueError("explicit stop read profile required")
        if self.valuation != "linear_contract_fx_unrounded_v1":
            raise ValueError("unsupported stop valuation hypothesis")
        if type(self.interval_ns) is not int or self.interval_ns != 5_000_000_000:
            raise ValueError("Dubai monitor stop cadence is five seconds")

    @property
    def loss_budget(self):
        return float(dubai.DubaiLivePolicy().stop_value)

    def descriptor(self):
        return {"role": "dubai_stop_component_on_modelled_execution",
                "strategy_id": dubai.CANDIDATE_ID,
                "strategy_fingerprint": dubai.CANDIDATE_FINGERPRINT,
                "loss_budget": self.loss_budget, "currency": "EUR",
                "reads": asdict(self.reads), "interval_ns": self.interval_ns,
                "valuation": self.valuation, "symbol_metadata": "declared_protection_profile",
                "native_valuation_verified": False, "complete_strategy_admitted": False}
