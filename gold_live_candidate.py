"""Frozen Gold Signals NOW demo policy selected by exact tick research."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math


CANDIDATE_ID = "gold_now_c490_v1"
CANDIDATE_FINGERPRINT = (
    "c4900550abae98de1500bf5b849072956175fdecda102fad69be9f7975cbf8d6"
)


def assert_demo_eur_account(
    evidence: dict | None,
    *,
    demo_trade_mode: int = 0,
) -> None:
    """Refuse a live candidate order unless MT5 still reports demo EUR."""
    evidence = evidence or {}
    trade_mode = evidence.get("trade_mode")
    trade_mode_name = str(evidence.get("trade_mode_name") or "").lower()
    currency = str(evidence.get("currency") or "").upper()
    if (
        trade_mode != int(demo_trade_mode)
        or trade_mode_name != "demo"
        or currency != "EUR"
    ):
        raise RuntimeError(
            "gold_now_c490_v1 requiere una cuenta MT5 demo EUR verificada"
        )


@dataclass(frozen=True)
class GoldGuardState:
    armed: bool = False
    triggered: bool = False
    peak_pl: float | None = None
    trigger_reason: str | None = None
    recovery_pending: bool = False


@dataclass(frozen=True)
class GoldGuardDecision:
    action: str
    reason: str | None
    observed_pl: float
    state: GoldGuardState


@dataclass(frozen=True)
class GoldLivePolicy:
    schema_version: int = 1
    target_mode: str = "none"
    target_value: float | None = None
    partial_fraction: float = 0.0
    runner_target: float | None = None
    be_mode: str = "price"
    be_trigger: float = 12.0
    stop_mode: str = "basket_money"
    stop_value: float = 100.0
    profit_lock_arm: float = 10.0
    profit_lock_giveback: float = 8.0
    time_exit_min: int = 40
    time_exit_mode: str = "loss_only"
    provider_management_mode: str = "ignore"

    # Live-only entry and safety metadata. These values intentionally do not
    # enter the research fingerprint because the selected candidate was a
    # management-only policy over the already-observed five-leg MT5 baskets.
    live_leg_count: int = 5
    live_volume_per_leg: float = 0.01

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        if self.target_mode != "none" or self.target_value is not None:
            raise ValueError("Gold candidate must not install a target")
        if self.partial_fraction != 0.0 or self.runner_target is not None:
            raise ValueError("Gold candidate must not take partial profit")
        if self.be_mode != "price":
            raise ValueError("Gold candidate break-even mode must remain price")
        if self.stop_mode != "basket_money":
            raise ValueError("Gold candidate stop must remain basket_money")
        if self.time_exit_mode != "loss_only":
            raise ValueError("Gold candidate time exit must remain loss_only")
        if self.provider_management_mode != "ignore":
            raise ValueError("Gold candidate must ignore provider management")
        if self.live_leg_count <= 0:
            raise ValueError("Gold live leg count must be positive")
        positive = (
            self.live_volume_per_leg,
            self.be_trigger,
            self.stop_value,
            self.profit_lock_arm,
            self.profit_lock_giveback,
            self.time_exit_min,
        )
        if any(
            not math.isfinite(float(value)) or float(value) <= 0
            for value in positive
        ):
            raise ValueError("Gold candidate thresholds must be positive")
        if self.profit_lock_giveback >= self.profit_lock_arm:
            raise ValueError("Gold profit lock must preserve positive money")

    @property
    def max_signal_volume(self) -> float:
        return round(self.live_leg_count * self.live_volume_per_leg, 8)

    @property
    def broker_loss_budget_per_leg(self) -> float:
        return float(self.stop_value) / int(self.live_leg_count)

    def research_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("live_leg_count")
        payload.pop("live_volume_per_leg")
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


def evaluate_guard(
    *,
    policy: GoldLivePolicy,
    state: GoldGuardState,
    total_pl: float,
    n_open: int,
    elapsed_min: float,
    money_evidence_complete: bool,
) -> GoldGuardDecision:
    observed = float(total_pl)
    elapsed = float(elapsed_min)
    if not math.isfinite(observed) or not math.isfinite(elapsed):
        raise ValueError("guard samples must be finite")
    if n_open <= 0:
        return GoldGuardDecision("none", None, observed, state)
    if state.triggered:
        if state.recovery_pending:
            recovered = replace(state, recovery_pending=False)
            return GoldGuardDecision("close", "recovery", observed, recovered)
        return GoldGuardDecision("none", None, observed, state)
    if not money_evidence_complete:
        return GoldGuardDecision(
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
        return GoldGuardDecision("close", "basket_stop", observed, triggered)

    peak = observed if state.peak_pl is None else max(state.peak_pl, observed)
    updated = replace(state, peak_pl=peak)
    if updated.armed:
        if observed <= peak - float(policy.profit_lock_giveback):
            triggered = replace(
                updated,
                triggered=True,
                trigger_reason="profit_lock",
            )
            return GoldGuardDecision("close", "profit_lock", observed, triggered)
    elif observed >= float(policy.profit_lock_arm):
        armed = replace(updated, armed=True)
        return GoldGuardDecision("arm", "profit_arm", observed, armed)

    if elapsed >= float(policy.time_exit_min) and observed <= 0.0:
        triggered = replace(
            updated,
            triggered=True,
            trigger_reason="loss_time_exit",
        )
        return GoldGuardDecision(
            "close", "loss_time_exit", observed, triggered,
        )
    return GoldGuardDecision("none", None, observed, updated)


def favourable_move(direction: str, entry_price: float, exit_price: float) -> float:
    direction = str(direction).upper()
    if direction not in {"BUY", "SELL"}:
        raise ValueError("direction must be BUY or SELL")
    entry = float(entry_price)
    current = float(exit_price)
    if not all(math.isfinite(value) and value > 0 for value in (entry, current)):
        raise ValueError("prices must be positive and finite")
    return current - entry if direction == "BUY" else entry - current


def market_comment(message_id: int, leg_index: int | None = None) -> str:
    """Return a short crash-safe MT5 marker for the frozen candidate."""
    message_id = int(message_id)
    if message_id <= 0:
        raise ValueError("message_id must be positive")
    if leg_index is None:
        return f"c2_{message_id}_gv1"
    leg_index = int(leg_index)
    if leg_index <= 0:
        raise ValueError("leg_index must be positive")
    return f"c2_{message_id}_B{leg_index}_gv1"


if GoldLivePolicy().fingerprint != CANDIDATE_FINGERPRINT:
    raise RuntimeError("Gold live candidate no longer matches research")
