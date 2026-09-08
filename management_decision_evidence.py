"""Small journal records for the exact inputs consumed by live management."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone

import causal_trace
import journal


CONTRACT = "management_decision_inputs_v1"
KINDS = (
    "gold_555_trailing", "gold_555_leg_protection", "gold_555_basket_guard",
    "dubai_basket_guard",
)
_STATE_FIELDS = (
    "status", "requested_close_reason", "all_filled_tickets", "pending_tickets",
    "candidate_hard_stops", "candidate_entry_prices_by_ticket",
    "basket_guard_armed", "basket_guard_triggered", "basket_guard_peak_pl",
    "basket_guard_trigger_reason", "basket_guard_recovery_pending",
    "basket_guard_close_tickets", "candidate_first_fill_at", "timestamp",
)


def signal_state(signal) -> dict:
    return {
        name: utc_text(value) if isinstance(value, datetime) else deepcopy(value)
        for name in _STATE_FIELDS for value in (getattr(signal, name, None),)
    }


def utc_text(value: datetime) -> str:
    """Signal/monitor naive datetimes follow the existing UTC contract."""
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(timezone.utc).isoformat()


def _event(signal_id, event, build_fields):
    try:
        return journal.event(signal_id, event, **build_fields())
    except Exception as exc:
        print(f"[Management Capture] {event} failed: {type(exc).__name__}")
        return None


@contextmanager
def capture_management_decision(signal, *, kind: str, inputs: dict):
    """Preserve one evaluation and its declared actions, without broker I/O."""
    active = causal_trace.current_context()
    with causal_trace.bind_internal_decision(
        message_revision_id=active.message_revision_id or signal.source_message_revision_id,
        parent_decision_id=active.decision_id or signal.source_decision_id,
        reason=kind,
    ) as decision:
        signal_id = f"{signal.channel}_{signal.message_id}"
        def common():
            return {
                "management_contract": CONTRACT,
                "management_kind": kind,
                "strategy_id": signal.live_strategy_id,
                "strategy_fingerprint": signal.live_strategy_fingerprint,
                "direction": signal.direction,
                "decision_id": decision.decision_id,
                "message_revision_id": decision.message_revision_id,
                "parent_decision_id": decision.parent_decision_id,
                "decision_reason": kind,
            }
        _event(
            signal_id, "bot_internal_decision_started", lambda: dict(
                common(), decision_inputs=deepcopy(inputs), state_before=signal_state(signal),
                observed_at_utc=datetime.now(timezone.utc).isoformat(),
            ),
        )
        outcome = {"result": None}
        status = "completed"
        error_type = None
        try:
            yield outcome
        except BaseException as exc:
            status = "error"
            error_type = type(exc).__name__
            raise
        finally:
            def completed_fields():
                result = outcome["result"]
                action_ids = causal_trace.declared_action_ids(decision)
                return dict(
                    common(), decision_status=status, error_type=error_type,
                    decision_result=asdict(result) if is_dataclass(result) else result,
                    state_after=signal_state(signal),
                    declared_action_ids=action_ids, declared_action_count=len(action_ids),
                )
            _event(
                signal_id, "bot_internal_decision", completed_fields,
            )
