"""Causal identifiers shared by Telegram, the journal, and MT5 actions."""

from __future__ import annotations

import contextvars
import hashlib
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class CausalContext:
    message_revision_id: str | None = None
    decision_id: str | None = None


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
    return _new_runtime_id("action")


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


def current_or_new_decision_id() -> str:
    return _current_context.get().decision_id or new_decision_id()


@contextmanager
def bind_message_revision(
    message_revision_id: str,
    *,
    decision_id: str | None = None,
) -> Iterator[CausalContext]:
    bound = CausalContext(
        message_revision_id=message_revision_id,
        decision_id=decision_id or new_decision_id(),
    )
    token = _current_context.set(bound)
    try:
        yield bound
    finally:
        _current_context.reset(token)
