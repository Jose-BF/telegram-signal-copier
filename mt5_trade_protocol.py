"""Strict JSON contracts for two-phase durable MT5 trade dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from mt5_protocol import BrokerOutcome, BrokerRequest, ImmutableJsonMapping
from mt5_read_protocol import MAX_REQUEST_BYTES, encode_message, finite_number, integer


TRADE_PROTOCOL_VERSION = 1
ENTRY_OPERATIONS = frozenset({"OPEN_MARKET", "PLACE_LIMIT"})
TRADE_OPERATIONS = frozenset({
    "OPEN_MARKET",
    "PLACE_LIMIT",
    "MODIFY_SLTP",
    "CLOSE_POSITION",
    "CANCEL_PENDING",
})


def validate_trade_request(request: BrokerRequest) -> None:
    request.to_dict()
    operation = request.intent_key.operation
    if operation not in TRADE_OPERATIONS:
        raise ValueError("unsupported trade operation")
    payload = dict(request.payload)
    required = {"symbol"}
    optional: set[str] = set()
    if operation == "OPEN_MARKET":
        required |= {
            "direction", "volume", "sl", "tp", "loss_budget",
            "protection_policy", "magic", "comment", "deviation",
        }
    elif operation == "PLACE_LIMIT":
        required |= {
            "direction", "volume", "price", "sl", "tp", "magic",
            "comment", "deviation",
        }
    elif operation == "MODIFY_SLTP":
        required |= {"ticket", "expected_magic", "new_sl", "new_tp"}
    elif operation == "CLOSE_POSITION":
        required |= {"ticket", "expected_magic", "deviation"}
    elif operation == "CANCEL_PENDING":
        required |= {"ticket", "expected_magic"}
    if set(payload) != required | optional:
        raise ValueError("invalid trade payload fields")

    symbol = payload["symbol"]
    if (
        not isinstance(symbol, str)
        or not symbol
        or len(symbol) > 64
        or any(char in symbol for char in "*,!")
    ):
        raise ValueError("invalid exact symbol")
    if "direction" in payload and payload["direction"] not in {"BUY", "SELL"}:
        raise ValueError("direction must be BUY or SELL")
    for name in ("volume", "price"):
        if name in payload:
            finite_number(payload[name], name, positive=True)
    for name in ("sl", "tp", "loss_budget", "new_sl", "new_tp"):
        if name in payload and payload[name] is not None:
            if operation == "MODIFY_SLTP" and name == "new_tp" and payload[name] == 0:
                continue
            finite_number(payload[name], name, positive=True)
    if operation == "MODIFY_SLTP" and payload["new_sl"] is None and payload["new_tp"] is None:
        raise ValueError("modify requires at least one level")
    for name in ("ticket", "magic", "expected_magic", "deviation"):
        if name in payload:
            value = payload[name]
            if name == "expected_magic" and value is None:
                raise ValueError("expected_magic is required for management")
            integer(value, name, 0 if name in {"magic", "deviation"} else 1, 2**63 - 1)
    if "comment" in payload and (
        not isinstance(payload["comment"], str) or len(payload["comment"]) > 31
    ):
        raise ValueError("invalid trade comment")
    if "protection_policy" in payload and payload["protection_policy"] not in {
        "required",
        "deferred_explicit",
    }:
        raise ValueError("invalid protection policy")
    encode_message(request.to_dict(), MAX_REQUEST_BYTES)


@dataclass(frozen=True)
class TradePreparation:
    request_id: str
    intent_id: str
    attempt_id: str
    action_id: str
    worker_session_id: str
    worker_pid: int
    prepared_monotonic: float
    native_request: Mapping[str, Any] | None = field(default=None, repr=False)
    evidence: Mapping[str, Any] = field(default_factory=dict, repr=False)
    error: str | None = None
    protocol_version: int = TRADE_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _validate_identity(self)
        integer(self.worker_pid, "worker_pid", 1, 2**32 - 1)
        finite_number(self.prepared_monotonic, "prepared_monotonic", positive=True)
        if self.protocol_version != TRADE_PROTOCOL_VERSION:
            raise ValueError("unsupported trade protocol version")
        if not isinstance(self.evidence, Mapping):
            raise ValueError("evidence must be an object")
        object.__setattr__(self, "evidence", ImmutableJsonMapping(self.evidence))
        if self.error is None:
            if not isinstance(self.native_request, Mapping) or not self.native_request:
                raise ValueError("successful preparation requires a native request")
            object.__setattr__(self, "native_request", ImmutableJsonMapping(self.native_request))
        elif self.native_request is not None:
            raise ValueError("failed preparation cannot expose a native request")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "intent_id": self.intent_id,
            "attempt_id": self.attempt_id,
            "action_id": self.action_id,
            "worker_session_id": self.worker_session_id,
            "worker_pid": self.worker_pid,
            "prepared_monotonic": self.prepared_monotonic,
            "native_request": (
                self.native_request.to_dict()
                if isinstance(self.native_request, ImmutableJsonMapping)
                else self.native_request
            ),
            "evidence": self.evidence.to_dict(),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TradePreparation":
        expected = {
            "protocol_version", "request_id", "intent_id", "attempt_id", "action_id",
            "worker_session_id", "worker_pid", "prepared_monotonic", "native_request",
            "evidence", "error",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("invalid trade preparation fields")
        return cls(**dict(value))


@dataclass(frozen=True)
class TradeResponse:
    request_id: str
    intent_id: str
    attempt_id: str
    action_id: str
    worker_session_id: str
    worker_pid: int
    started_monotonic: float
    completed_monotonic: float
    outcome: BrokerOutcome
    native_error: tuple[int, str] | None = None
    protocol_version: int = TRADE_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _validate_identity(self)
        integer(self.worker_pid, "worker_pid", 1, 2**32 - 1)
        finite_number(self.started_monotonic, "started_monotonic", positive=True)
        finite_number(self.completed_monotonic, "completed_monotonic", positive=True)
        if self.completed_monotonic < self.started_monotonic:
            raise ValueError("invalid trade response clock order")
        if not isinstance(self.outcome, BrokerOutcome):
            if not isinstance(self.outcome, Mapping):
                raise ValueError("invalid broker outcome")
            object.__setattr__(self, "outcome", BrokerOutcome.from_dict(self.outcome))
        if self.native_error is not None:
            code, message = self.native_error
            integer(code, "native error code", -2**31, 2**31 - 1)
            if not isinstance(message, str) or len(message) > 1024:
                raise ValueError("invalid native error")
            object.__setattr__(self, "native_error", (code, message))
        if self.protocol_version != TRADE_PROTOCOL_VERSION:
            raise ValueError("unsupported trade protocol version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "intent_id": self.intent_id,
            "attempt_id": self.attempt_id,
            "action_id": self.action_id,
            "worker_session_id": self.worker_session_id,
            "worker_pid": self.worker_pid,
            "started_monotonic": self.started_monotonic,
            "completed_monotonic": self.completed_monotonic,
            "outcome": self.outcome.to_dict(),
            "native_error": self.native_error,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TradeResponse":
        expected = {
            "protocol_version", "request_id", "intent_id", "attempt_id", "action_id",
            "worker_session_id", "worker_pid", "started_monotonic",
            "completed_monotonic", "outcome", "native_error",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("invalid trade response fields")
        return cls(**dict(value))


def _validate_identity(value: Any) -> None:
    for name in (
        "request_id", "intent_id", "attempt_id", "action_id", "worker_session_id"
    ):
        item = getattr(value, name)
        if not isinstance(item, str) or not item or len(item) > 128:
            raise ValueError(f"invalid {name}")
