"""Frozen Gold Signals NOW 555 demo policy.

The functions in this module are deliberately free of Telegram and MT5 side
effects. Runtime code supplies observed fills, executable quotes and basket
money; this module returns deterministic prices and decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math


CANDIDATE_ID = "gold_now_555_v1"
CANDIDATE_FINGERPRINT = (
    "555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0"
)


class Gold555AccountError(RuntimeError):
    """Raised when the frozen demo-only account contract is not satisfied."""


def assert_demo_eur_account(
    evidence: dict | None,
    *,
    demo_trade_mode: int = 0,
) -> None:
    evidence = evidence or {}
    trade_mode = evidence.get("trade_mode")
    trade_mode_name = str(evidence.get("trade_mode_name") or "").lower()
    currency = str(evidence.get("currency") or "").upper()
    if (
        trade_mode != int(demo_trade_mode)
        or trade_mode_name != "demo"
        or currency != "EUR"
    ):
        raise Gold555AccountError(
            "gold_now_555_v1 requiere una cuenta MT5 demo EUR verificada"
        )


def _direction_sign(direction: str) -> int:
    normalized = str(direction).upper()
    if normalized == "BUY":
        return 1
    if normalized == "SELL":
        return -1
    raise ValueError("direction must be BUY or SELL")


def _positive_price(value: float) -> float:
    price = float(value)
    if not math.isfinite(price) or price <= 0:
        raise ValueError("price must be positive and finite")
    return price


@dataclass(frozen=True)
class Gold555Policy:
    schema_version: int = 1
    entry_adverse: float = 1.0
    entry_reversal: float = 1.5
    entry_expiry_minutes: int = 30
    entry_volumes: tuple[float, ...] = (0.04, 0.03, 0.03, 0.03, 0.03)
    ladder_step: float = 1.5
    target_steps: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 2.5)
    trailing_distance: float = 30.0
    profit_arm_eur: float = 30.0
    profit_giveback_eur: float = 1.0
    non_negative_exit_minutes: int = 180
    provider_management_mode: str = "explicit_close_only"

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        if len(self.entry_volumes) != len(self.target_steps):
            raise ValueError("entry volumes and target steps must align")
        positive = (
            self.entry_adverse,
            self.entry_reversal,
            self.entry_expiry_minutes,
            self.ladder_step,
            self.trailing_distance,
            self.profit_arm_eur,
            self.profit_giveback_eur,
            self.non_negative_exit_minutes,
            *self.entry_volumes,
            *self.target_steps,
        )
        if any(
            not math.isfinite(float(value)) or float(value) <= 0
            for value in positive
        ):
            raise ValueError("Gold 555 thresholds must be positive and finite")
        if self.profit_giveback_eur >= self.profit_arm_eur:
            raise ValueError("profit lock must preserve positive basket money")
        if self.provider_management_mode != "explicit_close_only":
            raise ValueError("Gold 555 provider management contract changed")

    @property
    def max_signal_volume(self) -> float:
        return round(sum(self.entry_volumes), 8)

    def entry_levels(self, direction: str, first_real_fill: float) -> tuple[float, ...]:
        sign = _direction_sign(direction)
        anchor = _positive_price(first_real_fill)
        return tuple(
            round(anchor - sign * self.ladder_step * index, 8)
            for index in range(len(self.entry_volumes))
        )

    def target_price(
        self,
        direction: str,
        real_fill: float,
        leg_index: int,
    ) -> float:
        sign = _direction_sign(direction)
        fill = _positive_price(real_fill)
        index = int(leg_index)
        if index < 0 or index >= len(self.target_steps):
            raise IndexError("leg_index outside frozen Gold 555 ladder")
        return round(fill + sign * self.target_steps[index], 8)

    def initial_stop(self, direction: str, real_fill: float) -> float:
        sign = _direction_sign(direction)
        fill = _positive_price(real_fill)
        return round(fill - sign * self.trailing_distance, 8)

    def trailing_stop(
        self,
        direction: str,
        *,
        executable_price: float,
        current_stop: float | None,
    ) -> float | None:
        sign = _direction_sign(direction)
        current = _positive_price(executable_price)
        candidate = round(current - sign * self.trailing_distance, 8)
        if current_stop in (None, 0, 0.0):
            return candidate
        existing = _positive_price(float(current_stop))
        if sign > 0 and candidate > existing:
            return candidate
        if sign < 0 and candidate < existing:
            return candidate
        return None


@dataclass(frozen=True)
class Gold555GuardState:
    armed: bool = False
    triggered: bool = False
    peak_pl: float | None = None
    trigger_reason: str | None = None
    recovery_pending: bool = False


@dataclass(frozen=True)
class Gold555GuardDecision:
    action: str
    reason: str | None
    observed_pl: float
    state: Gold555GuardState


def evaluate_guard(
    *,
    policy: Gold555Policy,
    state: Gold555GuardState,
    total_pl: float,
    n_open: int,
    elapsed_min: float,
    money_evidence_complete: bool,
) -> Gold555GuardDecision:
    observed = float(total_pl)
    elapsed = float(elapsed_min)
    if not math.isfinite(observed) or not math.isfinite(elapsed):
        raise ValueError("guard samples must be finite")
    if int(n_open) <= 0:
        return Gold555GuardDecision("none", None, observed, state)
    if state.triggered:
        if state.recovery_pending:
            recovered = replace(state, recovery_pending=False)
            return Gold555GuardDecision("close", "recovery", observed, recovered)
        return Gold555GuardDecision("none", None, observed, state)
    if not money_evidence_complete:
        return Gold555GuardDecision(
            "evidence_incomplete",
            "money_evidence_incomplete",
            observed,
            state,
        )

    peak = observed if state.peak_pl is None else max(state.peak_pl, observed)
    updated = replace(state, peak_pl=peak)
    if updated.armed:
        if observed <= peak - float(policy.profit_giveback_eur):
            triggered = replace(
                updated,
                triggered=True,
                trigger_reason="profit_lock",
            )
            return Gold555GuardDecision(
                "close", "profit_lock", observed, triggered
            )
    elif observed >= float(policy.profit_arm_eur):
        armed = replace(updated, armed=True)
        return Gold555GuardDecision("arm", "profit_arm", observed, armed)

    if elapsed >= float(policy.non_negative_exit_minutes) and observed >= 0.0:
        triggered = replace(
            updated,
            triggered=True,
            trigger_reason="non_negative_time_exit",
        )
        return Gold555GuardDecision(
            "close", "non_negative_time_exit", observed, triggered
        )
    return Gold555GuardDecision("none", None, observed, updated)


def market_comment(message_id: int, leg_index: int | None = None) -> str:
    message_id = int(message_id)
    if message_id <= 0:
        raise ValueError("message_id must be positive")
    if leg_index is None:
        return f"c2_{message_id}_g55"
    index = int(leg_index)
    if index <= 0:
        raise ValueError("leg_index must be positive")
    return f"c2_{message_id}_B{index}_g55"


def is_provider_close_action(action: str) -> bool:
    """Match the exact close predicate used by the frozen replay."""
    normalized = str(action or "").upper()
    return "CLOSE" in normalized or normalized in {"EXIT", "CERRAR"}
