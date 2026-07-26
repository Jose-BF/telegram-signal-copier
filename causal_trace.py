"""Causal identifiers shared by Telegram, the journal, and MT5 actions."""

from __future__ import annotations

import contextvars
import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterable, Iterator, TypeVar


_T = TypeVar("_T")
_PRIMARY_MARKET_COMMENT_RE = re.compile(r"^c([12])_([1-9]\d*)$")
_MARKET_SUCCESS_RETCODES = {10009}


class _ActionCollector:
    def __init__(self) -> None:
        self._lock = Lock()
        self._action_ids: list[str] = []
        self._seen: set[str] = set()

    def register(self, action_id: str) -> None:
        with self._lock:
            if action_id in self._seen:
                return
            self._seen.add(action_id)
            self._action_ids.append(action_id)

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._action_ids)


@dataclass(frozen=True)
class CausalContext:
    message_revision_id: str | None = None
    decision_id: str | None = None
    decision_kind: str | None = None
    parent_decision_id: str | None = None
    decision_reason: str | None = None
    action_collector: _ActionCollector | None = field(
        default=None,
        compare=False,
        repr=False,
    )


_current_context: contextvars.ContextVar[CausalContext] = (
    contextvars.ContextVar(
        "causal_trace_context",
        default=CausalContext(),
    )
)


def _new_runtime_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def new_decision_id() -> str:
    return _new_runtime_id("decision")


def new_action_id() -> str:
    action_id = _new_runtime_id("action")
    register_action_id(action_id)
    return action_id


def new_attempt_id() -> str:
    return _new_runtime_id("attempt")


def new_event_id() -> str:
    return _new_runtime_id("event")


def new_session_id() -> str:
    return _new_runtime_id("session")


def message_revision_id(
    *,
    chat_id: int,
    message_id: int,
    revision_token: str,
    text_sha1: str | None,
    media_sha256: str | None,
) -> str:
    payload = {
        "chat_id": int(chat_id),
        "message_id": int(message_id),
        "revision_token": str(revision_token),
        "text_sha1": text_sha1,
        "media_sha256": media_sha256,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"msgrev_{hashlib.sha256(canonical).hexdigest()}"


def current_fields() -> dict[str, str]:
    current = _current_context.get()
    fields = {}
    if current.message_revision_id is not None:
        fields["message_revision_id"] = current.message_revision_id
    if current.decision_id is not None:
        fields["decision_id"] = current.decision_id
    return fields


def current_message_revision_id() -> str | None:
    return _current_context.get().message_revision_id


def current_decision_id() -> str | None:
    return _current_context.get().decision_id


def current_or_new_decision_id() -> str:
    return current_decision_id() or new_decision_id()


def current_decision_kind() -> str | None:
    return _current_context.get().decision_kind


def current_context() -> CausalContext:
    return _current_context.get()


def register_action_id(action_id: str) -> None:
    collector = _current_context.get().action_collector
    if collector is not None:
        collector.register(str(action_id))


def declared_action_ids(
    context: CausalContext | None = None,
) -> list[str]:
    selected = context or _current_context.get()
    if selected.action_collector is None:
        return []
    return selected.action_collector.snapshot()


def context_bound_call(
    function: Callable[..., _T],
    *args: Any,
    **kwargs: Any,
) -> Callable[[], _T]:
    """Capture only the causal context for one worker-thread call."""
    captured = _current_context.get()

    def invoke() -> _T:
        token = _current_context.set(captured)
        try:
            return function(*args, **kwargs)
        finally:
            _current_context.reset(token)

    return invoke


def signal_origin_index(
    rows: Iterable[dict],
) -> tuple[dict[str, dict[str, str]], dict[str, list[dict[str, str]]]]:
    """Index unambiguous market-entry origins; expose conflicts explicitly."""
    materialized = [dict(row) for row in rows]
    successful_actions = {
        str(row.get("action_id"))
        for row in materialized
        if (
            row.get("action_id")
            and (
                (
                    row.get("ev") == "mt5_order_result"
                    and row.get("retcode") in _MARKET_SUCCESS_RETCODES
                )
                or row.get("ev") == "market_fill_recovered_from_non_done"
            )
        )
    }
    requests: dict[
        str,
        list[tuple[tuple[str, str], str | None]],
    ] = {}
    for row in materialized:
        if (
            row.get("ev") != "mt5_order_requested"
            or row.get("order_kind") != "market"
        ):
            continue
        sig_id = row.get("sig")
        comment_match = _PRIMARY_MARKET_COMMENT_RE.fullmatch(
            str(row.get("comment") or "")
        )
        message_revision_id = row.get("message_revision_id")
        decision_id = row.get("decision_id")
        if not (
            sig_id
            and comment_match
            and str(sig_id) == (
                f"canal{comment_match.group(1)}_"
                f"{comment_match.group(2)}"
            )
            and message_revision_id
            and decision_id
        ):
            continue
        requests.setdefault(str(sig_id), []).append((
            (
                str(message_revision_id),
                str(decision_id),
            ),
            str(row["action_id"]) if row.get("action_id") else None,
        ))

    origins = {}
    conflicts = {}
    for sig_id, candidate_requests in sorted(requests.items()):
        confirmed_pairs = {
            pair
            for pair, action_id in candidate_requests
            if action_id in successful_actions
        }
        pairs = confirmed_pairs or {
            pair for pair, _ in candidate_requests
        }
        ordered = sorted(pairs)
        if len(ordered) == 1:
            message_revision_id, decision_id = ordered[0]
            origins[sig_id] = {
                "message_revision_id": message_revision_id,
                "decision_id": decision_id,
            }
            continue
        conflicts[sig_id] = [
            {
                "message_revision_id": message_revision_id,
                "decision_id": decision_id,
            }
            for message_revision_id, decision_id in ordered
        ]
    return origins, conflicts


def load_signal_origin_index(
    path: str | Path,
) -> tuple[
    dict[str, dict[str, str]],
    dict[str, list[dict[str, str]]],
    list[int],
]:
    """Load only causal market-origin rows without retaining the full log."""
    source = Path(path)
    if not source.exists():
        return {}, {}, []

    candidates = []
    invalid_lines = []
    with source.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            try:
                stripped = raw_line.decode("utf-8").strip()
            except UnicodeDecodeError:
                invalid_lines.append(line_number)
                continue
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                invalid_lines.append(line_number)
                continue
            if not isinstance(row, dict):
                invalid_lines.append(line_number)
                continue
            if (
                (
                    row.get("ev") == "mt5_order_requested"
                    and row.get("order_kind") == "market"
                )
                or row.get("ev") in {
                    "mt5_order_result",
                    "market_fill_recovered_from_non_done",
                }
            ):
                candidates.append(row)

    origins, conflicts = signal_origin_index(candidates)
    return origins, conflicts, invalid_lines


@contextmanager
def bind_message_revision(
    message_revision_id: str,
    *,
    decision_id: str | None = None,
) -> Iterator[CausalContext]:
    bound = CausalContext(
        message_revision_id=message_revision_id,
        decision_id=decision_id or new_decision_id(),
        decision_kind="telegram",
        action_collector=_ActionCollector(),
    )
    token = _current_context.set(bound)
    try:
        yield bound
    finally:
        _current_context.reset(token)


@contextmanager
def bind_internal_decision(
    *,
    message_revision_id: str | None,
    parent_decision_id: str | None,
    reason: str,
    decision_id: str | None = None,
) -> Iterator[CausalContext]:
    bound = CausalContext(
        message_revision_id=message_revision_id,
        decision_id=decision_id or new_decision_id(),
        decision_kind="internal",
        parent_decision_id=parent_decision_id,
        decision_reason=str(reason),
        action_collector=_ActionCollector(),
    )
    token = _current_context.set(bound)
    try:
        yield bound
    finally:
        _current_context.reset(token)


@contextmanager
def detached_context() -> Iterator[CausalContext]:
    bound = CausalContext()
    token = _current_context.set(bound)
    try:
        yield bound
    finally:
        _current_context.reset(token)
