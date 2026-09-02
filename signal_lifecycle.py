"""Pure lifecycle decisions for live strategy signals.

This module never calls Telegram or MT5.  It converts durable signal state and
broker observations into an auditable decision consumed by runtime adapters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable

from strategy_runtime_contract import strategy_contract_by_id


class TerminalCause(str, Enum):
    AUTOMATIC_FLAT = "automatic_flat"
    PROVIDER_CLOSE = "provider_close"
    STRATEGY_STOP = "strategy_stop"
    TIME_EXIT = "time_exit"
    RETRACTION = "retraction"
    OPERATOR_CLOSE = "operator_close"
    STARTUP_RECOVERY = "startup_recovery"


_EXPLICIT_CAUSES = frozenset({
    TerminalCause.PROVIDER_CLOSE,
    TerminalCause.STRATEGY_STOP,
    TerminalCause.TIME_EXIT,
    TerminalCause.RETRACTION,
    TerminalCause.OPERATOR_CLOSE,
})


@dataclass(frozen=True)
class LifecycleDecision:
    action: str
    reason: str
    cause: str
    observed_at_utc: str
    open_position_count: int
    eligible_entry_indexes: tuple[int, ...] = ()
    cancelled_entry_indexes: tuple[int, ...] = ()
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.action not in {"keep_alive", "defer", "finalize"}:
            raise ValueError("unsupported lifecycle action")

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["eligible_entry_indexes"] = list(
            self.eligible_entry_indexes
        )
        payload["cancelled_entry_indexes"] = list(
            self.cancelled_entry_indexes
        )
        payload["blockers"] = list(self.blockers)
        return payload


def _utc_naive(value: datetime | None) -> datetime:
    observed = datetime.utcnow() if value is None else value
    if observed.tzinfo is not None:
        return observed.astimezone(timezone.utc).replace(tzinfo=None)
    return observed


def _indexes(values: Iterable[object]) -> set[int]:
    normalized: set[int] = set()
    for value in values:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index >= 0:
            normalized.add(index)
    return normalized


def _plan_indexes(signal) -> tuple[int, ...]:
    indexes: list[int] = []
    for position, row in enumerate(
        list(getattr(signal, "candidate_entry_legs", None) or [])
    ):
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index", position))
        except (TypeError, ValueError):
            continue
        if index >= 0:
            indexes.append(index)
    return tuple(sorted(set(indexes)))


def _entry_state(signal) -> tuple[tuple[int, ...], tuple[int, ...]]:
    planned = _plan_indexes(signal)
    filled = _indexes(
        getattr(signal, "candidate_filled_leg_indexes", None) or []
    )
    if getattr(signal, "market_ticket", None) is not None and 0 in planned:
        filled.add(0)
    filled.update(_indexes(
        getattr(signal, "lifecycle_settled_entry_indexes", None) or []
    ))
    cancelled = _indexes(
        getattr(signal, "lifecycle_cancelled_entry_indexes", None) or []
    )
    pending = tuple(
        index for index in planned
        if index not in filled and index not in cancelled
    )
    return planned, pending


def _contract_blockers(signal) -> tuple[object | None, tuple[str, ...]]:
    strategy_id = getattr(signal, "live_strategy_id", None)
    if not strategy_id:
        if _plan_indexes(signal):
            return None, ("strategy_contract_missing",)
        return None, ()
    try:
        contract = strategy_contract_by_id(strategy_id)
    except KeyError:
        return None, ("strategy_contract_unknown",)
    if getattr(signal, "live_strategy_fingerprint", None) != (
        contract.strategy_fingerprint
    ):
        return contract, ("strategy_fingerprint_mismatch",)
    if contract.terminal.pending_entry_policy == "until_expiry":
        planned = _plan_indexes(signal)
        expected = tuple(range(len(contract.entry.volumes)))
        if planned != expected:
            return contract, ("entry_plan_incomplete_or_mutated",)
        expires_at = getattr(signal, "candidate_entry_expires_at", None)
        if not isinstance(expires_at, datetime):
            return contract, ("entry_expiry_missing",)
    return contract, ()


def _explicit_cause_from_signal(signal) -> TerminalCause | None:
    reason = str(getattr(signal, "requested_close_reason", None) or "").upper()
    if "RETRACT" in reason:
        return TerminalCause.RETRACTION
    if "PROVIDER" in reason or reason in {"CLOSE", "CLOSE_ALL"}:
        return TerminalCause.PROVIDER_CLOSE
    if "TIME" in reason:
        return TerminalCause.TIME_EXIT
    if "STOP" in reason or getattr(signal, "basket_guard_triggered", False):
        return TerminalCause.STRATEGY_STOP
    if "OPERATOR" in reason or "MANUAL" in reason:
        return TerminalCause.OPERATOR_CLOSE
    return None


def terminal_cause_for_signal(
    signal,
    *,
    default: TerminalCause = TerminalCause.AUTOMATIC_FLAT,
) -> TerminalCause:
    return _explicit_cause_from_signal(signal) or default


def evaluate_terminal_request(
    signal,
    *,
    cause: TerminalCause | str,
    open_position_count: int,
    observed_at: datetime | None = None,
    positions_complete: bool = True,
) -> LifecycleDecision:
    normalized_cause = TerminalCause(cause)
    observed = _utc_naive(observed_at)
    open_count = int(open_position_count)
    if open_count < 0:
        raise ValueError("open_position_count cannot be negative")

    planned, pending = _entry_state(signal)
    contract, blockers = _contract_blockers(signal)
    explicit = normalized_cause in _EXPLICIT_CAUSES
    cancelled = pending if explicit else ()

    common = {
        "cause": normalized_cause.value,
        "observed_at_utc": observed.isoformat(timespec="milliseconds"),
        "open_position_count": open_count,
        "cancelled_entry_indexes": cancelled,
    }
    if not positions_complete:
        return LifecycleDecision(
            action="defer",
            reason="position_evidence_incomplete",
            blockers=("position_evidence_incomplete",),
            **common,
        )

    if explicit:
        if open_count > 0:
            return LifecycleDecision(
                action="defer",
                reason="open_positions",
                **common,
            )
        return LifecycleDecision(
            action="finalize",
            reason=normalized_cause.value,
            **common,
        )

    if open_count > 0:
        return LifecycleDecision(
            action="defer",
            reason="open_positions",
            **common,
        )

    if blockers:
        return LifecycleDecision(
            action="keep_alive",
            reason="lifecycle_evidence_incomplete",
            blockers=blockers,
            **common,
        )

    eligible: tuple[int, ...] = ()
    if contract is not None and (
        contract.terminal.pending_entry_policy == "until_expiry"
    ):
        expires_at = _utc_naive(
            getattr(signal, "candidate_entry_expires_at", None)
        )
        if observed <= expires_at:
            eligible = pending

    if eligible:
        return LifecycleDecision(
            action="keep_alive",
            reason="eligible_entry_intents",
            eligible_entry_indexes=eligible,
            **common,
        )
    return LifecycleDecision(
        action="finalize",
        reason="no_eligible_entry_intents",
        **common,
    )


def apply_lifecycle_decision(signal, decision: LifecycleDecision) -> dict:
    cancelled = _indexes(
        getattr(signal, "lifecycle_cancelled_entry_indexes", None) or []
    )
    cancelled.update(decision.cancelled_entry_indexes)
    signal.lifecycle_cancelled_entry_indexes = sorted(cancelled)
    if decision.cause != TerminalCause.AUTOMATIC_FLAT.value:
        signal.lifecycle_terminal_cause = decision.cause
    if decision.action == "keep_alive":
        signal.lifecycle_state = "temporarily_flat"
    elif decision.action == "defer":
        signal.lifecycle_state = "closing"
    else:
        signal.lifecycle_state = "terminal_ready"
    payload = decision.to_dict()
    signal.lifecycle_last_decision = payload
    return dict(payload)

