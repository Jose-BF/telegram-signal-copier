"""Frozen Dubai Investing demo policy derived from the tick replay research."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
import hashlib
import json
import math


CANDIDATE_ID = "dubai_balanced_v1"
CANDIDATE_FINGERPRINT = (
    "32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631"
)


@dataclass(frozen=True)
class DubaiEntryLeg:
    index: int
    volume: float
    trigger_price: float | None
    expires_at: datetime
    opened_immediately: bool = False


@dataclass(frozen=True)
class DubaiGuardState:
    armed: bool = False
    triggered: bool = False
    peak_pl: float | None = None
    trigger_reason: str | None = None
    recovery_pending: bool = False


@dataclass(frozen=True)
class DubaiGuardDecision:
    action: str
    reason: str | None
    observed_pl: float
    state: DubaiGuardState


@dataclass(frozen=True)
class DubaiLivePolicy:
    schema_version: int = 1
    entry_mode: str = "signal_market"
    entry_value: float | None = None
    entry_confirmation_value: float | None = None
    entry_expiry_min: int = 15
    entry_ladder_mode: str = "adverse"
    entry_ladder_step: float = 4.0
    leg_count: int = 3
    volume_weights: tuple[float, ...] = (0.01, 0.04, 0.04)
    target_mode: str = "none"
    target_value: float | None = None
    partial_fraction: float = 0.0
    runner_target: float | None = None
    be_mode: str = "none"
    be_trigger: float | None = None
    stop_mode: str = "basket_money"
    stop_value: float = 25.0
    profit_lock_arm: float = 10.0
    profit_lock_giveback: float = 2.0
    time_exit_min: int = 40
    time_exit_mode: str = "loss_only"
    provider_management_mode: str = "exact"
    context_filter_mode: str = "none"
    context_filter_value: float | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        if self.entry_mode != "signal_market":
            raise ValueError("entry_mode must remain signal_market")
        if self.entry_ladder_mode != "adverse":
            raise ValueError("entry_ladder_mode must remain adverse")
        if self.leg_count < 1 or len(self.volume_weights) != self.leg_count:
            raise ValueError("volume_weights must match leg_count")
        finite_positive = (
            self.entry_expiry_min,
            self.entry_ladder_step,
            self.stop_value,
            self.profit_lock_arm,
            self.profit_lock_giveback,
            self.time_exit_min,
            *self.volume_weights,
        )
        if any(not math.isfinite(float(value)) or float(value) <= 0
               for value in finite_positive):
            raise ValueError("candidate thresholds and volumes must be positive")
        if self.target_mode != "none" or self.target_value is not None:
            raise ValueError("candidate must not install a target")
        if self.be_mode != "none" or self.be_trigger is not None:
            raise ValueError("candidate must not install breakeven")
        if self.stop_mode != "basket_money":
            raise ValueError("candidate stop must use aggregate money")
        if self.time_exit_mode != "loss_only":
            raise ValueError("candidate time exit must remain loss_only")
        if self.provider_management_mode != "exact":
            raise ValueError("provider management mode must remain exact")
        if self.context_filter_mode != "none":
            raise ValueError("candidate must not filter accepted signals")

    @property
    def max_signal_volume(self) -> float:
        return round(sum(self.volume_weights), 8)

    def research_payload(self) -> dict:
        payload = asdict(self)
        payload["volume_weights"] = list(self.volume_weights)
        return payload

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.research_payload(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def entry_plan(
        self,
        *,
        direction: str,
        anchor_price: float,
        opened_at: datetime,
    ) -> tuple[DubaiEntryLeg, ...]:
        direction = str(direction).upper()
        if direction not in {"BUY", "SELL"}:
            raise ValueError("direction must be BUY or SELL")
        anchor = float(anchor_price)
        if not math.isfinite(anchor) or anchor <= 0:
            raise ValueError("anchor_price must be positive and finite")
        expires_at = opened_at + timedelta(minutes=self.entry_expiry_min)
        sign = -1.0 if direction == "BUY" else 1.0
        legs = []
        for index, volume in enumerate(self.volume_weights):
            trigger = None
            if index:
                trigger = round(
                    anchor + sign * self.entry_ladder_step * index,
                    8,
                )
            legs.append(DubaiEntryLeg(
                index=index,
                volume=float(volume),
                trigger_price=trigger,
                expires_at=expires_at,
                opened_immediately=index == 0,
            ))
        return tuple(legs)


def evaluate_guard(
    *,
    policy: DubaiLivePolicy,
    state: DubaiGuardState,
    total_pl: float,
    n_open: int,
    elapsed_min: float,
    money_evidence_complete: bool,
) -> DubaiGuardDecision:
    observed = float(total_pl)
    elapsed = float(elapsed_min)
    if not math.isfinite(observed) or not math.isfinite(elapsed):
        raise ValueError("guard samples must be finite")
    if n_open <= 0:
        return DubaiGuardDecision("none", None, observed, state)
    if state.triggered:
        if state.recovery_pending:
            recovered = replace(state, recovery_pending=False)
            return DubaiGuardDecision("close", "recovery", observed, recovered)
        return DubaiGuardDecision("none", None, observed, state)

    if not money_evidence_complete:
        return DubaiGuardDecision(
            "evidence_incomplete",
            "money_evidence_incomplete",
            observed,
            state,
        )

    if observed <= -float(policy.stop_value):
        triggered = replace(
            state,
            triggered=True,
            trigger_reason="basket_stop",
        )
        return DubaiGuardDecision("close", "basket_stop", observed, triggered)

    peak = observed if state.peak_pl is None else max(state.peak_pl, observed)
    updated = replace(state, peak_pl=peak)

    if updated.armed:
        lock_level = peak - float(policy.profit_lock_giveback)
        if observed <= lock_level:
            triggered = replace(
                updated,
                triggered=True,
                trigger_reason="profit_lock",
            )
            return DubaiGuardDecision(
                "close", "profit_lock", observed, triggered,
            )
    elif observed >= float(policy.profit_lock_arm):
        armed = replace(updated, armed=True)
        return DubaiGuardDecision("arm", "profit_arm", observed, armed)

    if (
        elapsed >= float(policy.time_exit_min)
        and observed <= 0.0
    ):
        triggered = replace(
            updated,
            triggered=True,
            trigger_reason="loss_time_exit",
        )
        return DubaiGuardDecision(
            "close", "loss_time_exit", observed, triggered,
        )

    return DubaiGuardDecision("none", None, observed, updated)


def is_provider_close_action(action: str) -> bool:
    normalized = str(action or "").upper()
    return "CLOSE" in normalized or normalized in {"EXIT", "CERRAR"}


if DubaiLivePolicy().fingerprint != CANDIDATE_FINGERPRINT:
    raise RuntimeError("Dubai live candidate no longer matches research")
