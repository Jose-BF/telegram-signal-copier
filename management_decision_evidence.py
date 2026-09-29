"""Small journal records for the exact inputs consumed by live management."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import os
import time

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


# Journal volume guard (29/09/2026): an open basket is evaluated about once per second and 98 % of
# trade_events.jsonl were "no action, same state" pairs. A decision is written only when it declares an
# action, returns a non-"none" action, fails, changes the durable state, or once per
# BOT_MANAGEMENT_NOOP_SAMPLE_SEC per signal and kind (default 60 s), so the trail stays auditable.
try:
    NOOP_SAMPLE_SEC = max(0.0, float(os.getenv("BOT_MANAGEMENT_NOOP_SAMPLE_SEC", "60")))
except ValueError:
    NOOP_SAMPLE_SEC = 60.0
_VOLATILE_STATE = {"timestamp"}
_last_written: dict[tuple[str, str], tuple[float, str]] = {}


def _state_fingerprint(state: dict) -> str:
    try:
        return json.dumps({k: v for k, v in state.items() if k not in _VOLATILE_STATE}, sort_keys=True, default=str)
    except Exception:
        return repr(sorted(state.items(), key=lambda kv: kv[0]))


def _result_action(result):
    if is_dataclass(result):
        result = asdict(result)
    if isinstance(result, dict):
        return result.get("action")
    return None


def _should_write_start(key, state_before) -> bool:
    """Write the start record immediately (keeps its order before the decision's actions) unless this
    evaluation repeats the last recorded state within the sampling window."""
    last = _last_written.get(key)
    return (
        last is None
        or last[1] != _state_fingerprint(state_before)
        or time.monotonic() - last[0] >= NOOP_SAMPLE_SEC
    )


def _should_write(key, *, status, action_count, result, state_after) -> bool:
    now = time.monotonic()
    fingerprint = _state_fingerprint(state_after)
    last = _last_written.get(key)
    write = (
        status != "completed"
        or action_count > 0
        or _result_action(result) not in (None, "none")
        or last is None
        or last[1] != fingerprint
        or now - last[0] >= NOOP_SAMPLE_SEC
    )
    if write:
        _last_written[key] = (now, fingerprint)
    return write


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
        try:
            started_fields = dict(
                common(), decision_inputs=deepcopy(inputs), state_before=signal_state(signal),
                observed_at_utc=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            print(f"[Management Capture] bot_internal_decision_started failed: {type(exc).__name__}")
            started_fields = None
        started_written = False
        if started_fields is not None and _should_write_start((signal_id, kind), started_fields["state_before"]):
            _event(signal_id, "bot_internal_decision_started", lambda: started_fields)
            started_written = True
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
            try:
                fields = completed_fields()
            except Exception as exc:
                print(f"[Management Capture] bot_internal_decision failed: {type(exc).__name__}")
                fields = None
            if started_written or fields is None or _should_write(
                (signal_id, kind), status=status, action_count=fields.get("declared_action_count", 0),
                result=outcome["result"], state_after=fields.get("state_after") or {},
            ):
                if started_written and fields is not None:
                    _last_written[(signal_id, kind)] = (time.monotonic(), _state_fingerprint(fields.get("state_after") or {}))
                if started_fields is not None and not started_written:
                    _event(signal_id, "bot_internal_decision_started", lambda: started_fields)
                if fields is not None:
                    _event(signal_id, "bot_internal_decision", lambda: fields)
