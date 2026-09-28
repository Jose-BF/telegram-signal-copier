"""Bounded, JSON-only read protocol; deliberately separate from trade intents."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import math
import time
import uuid
from typing import Any, Mapping

from mt5_protocol import ImmutableJsonMapping, LookupState

READ_PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 16_384
MAX_RESPONSE_BYTES = 524_288
MAX_RECORDS = 10_000
MAX_HISTORY_SECONDS = 86_400
MAX_HISTORY_MILLISECONDS = MAX_HISTORY_SECONDS * 1000


class ReadOperation(str, Enum):
    INITIALIZE = "initialize"
    SHUTDOWN = "shutdown"
    ACCOUNT = "account_info"
    TERMINAL = "terminal_info"
    SYMBOL = "symbol_info"
    TICK = "symbol_info_tick"
    POSITIONS = "positions_get"
    ORDERS = "orders_get"
    DEALS = "history_deals_get"
    DEALS_POSITION = "history_deals_get_position"
    HISTORY_ORDERS = "history_orders_get"
    RATES = "copy_rates_from_pos"
    TICKS = "copy_ticks_from"
    TICKS_RANGE = "copy_ticks_range"
    PROFIT = "order_calc_profit"
    MARGIN = "order_calc_margin"


COLLECTION_OPERATIONS = frozenset({
    ReadOperation.POSITIONS, ReadOperation.ORDERS, ReadOperation.DEALS,
    ReadOperation.DEALS_POSITION, ReadOperation.HISTORY_ORDERS,
    ReadOperation.RATES, ReadOperation.TICKS, ReadOperation.TICKS_RANGE,
})


def finite_number(value: Any, name: str, *, positive: bool = False) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (positive and value <= 0)):
        raise ValueError(f"invalid {name}")
    return float(value)


def integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid {name}")
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"invalid {name}")
    return value


def encode_message(value: Mapping[str, Any], limit: int) -> bytes:
    chunks, size = [], 0
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    for chunk in encoder.iterencode(value):
        part = chunk.encode("ascii")
        size += len(part)
        if size > limit:
            raise ValueError("message_size_limit")
        chunks.append(part)
    return b"".join(chunks)


def decode_message(raw: bytes, limit: int) -> dict:
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise ValueError("message_size_limit")
    def invalid_constant(_value):
        raise ValueError("non_finite_json")
    value = json.loads(raw, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("message must be an object")
    return value


def validate_params(operation: ReadOperation, params: Mapping[str, Any]) -> None:
    required, optional = set(), set()
    if operation in {ReadOperation.SYMBOL, ReadOperation.TICK}:
        required = {"symbol"}
    elif operation in {ReadOperation.POSITIONS, ReadOperation.ORDERS}:
        optional = {"symbol", "ticket"}
        if len(params) > 1:
            raise ValueError("choose symbol or ticket, not both")
    elif operation in {ReadOperation.DEALS, ReadOperation.HISTORY_ORDERS}:
        required, optional = {"date_from", "date_to"}, {"symbol"}
    elif operation is ReadOperation.DEALS_POSITION:
        required = {"position"}
    elif operation is ReadOperation.RATES:
        required = {"symbol", "timeframe", "start_pos", "count"}
    elif operation is ReadOperation.TICKS:
        required = {"symbol", "count", "flags"}
        optional = {"date_from", "date_from_msc"}
        if ("date_from" in params) == ("date_from_msc" in params):
            raise ValueError("choose one tick start clock")
    elif operation is ReadOperation.TICKS_RANGE:
        required = {"symbol", "flags"}
        seconds = {"date_from", "date_to"}
        milliseconds = {"date_from_msc", "date_to_msc"}
        clock_fields = params.keys() & (seconds | milliseconds)
        if clock_fields not in (seconds, milliseconds):
            raise ValueError("choose one tick range clock")
        optional = seconds | milliseconds
    elif operation is ReadOperation.PROFIT:
        required = {"symbol", "action", "volume", "price_open", "price_close"}
    elif operation is ReadOperation.MARGIN:
        required = {"symbol", "action", "volume", "price"}
    if not required <= params.keys() or params.keys() - required - optional:
        raise ValueError("invalid operation parameters")
    for key, value in params.items():
        if key == "symbol":
            _identifier(value, key)
            if any(c in value for c in "*,!"):
                raise ValueError("symbol must be exact, not a group expression")
        elif key == "count":
            integer(value, key, 1, MAX_RECORDS)
        elif key in {"ticket", "position"}:
            integer(value, key, 1, 2**64 - 1)
        elif key in {
            "date_from", "date_to", "date_from_msc", "date_to_msc",
            "start_pos", "timeframe",
        }:
            integer(value, key, 0, 2**53 - 1)
        elif key == "action":
            integer(value, key, 0, 1)
        elif key == "flags":
            integer(value, key, -1, 2)
            if value not in {-1, 1, 2}:
                raise ValueError("invalid tick flags")
        else:
            finite_number(value, key, positive=True)
    if "date_to_msc" in params:
        span = params["date_to_msc"] - params["date_from_msc"]
        if not 0 <= span <= MAX_HISTORY_MILLISECONDS:
            raise ValueError("history_window_limit")
    elif "date_to" in params:
        span = params["date_to"] - params["date_from"]
        if not 0 <= span <= MAX_HISTORY_SECONDS:
            raise ValueError("history_window_limit")


@dataclass(frozen=True)
class ReadRequest:
    operation: ReadOperation
    params: Mapping[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    protocol_version: int = READ_PROTOCOL_VERSION

    def __post_init__(self):
        object.__setattr__(self, "operation", ReadOperation(self.operation))
        if self.protocol_version != READ_PROTOCOL_VERSION or type(self.protocol_version) is not int:
            raise ValueError("unsupported read protocol version")
        _identifier(self.request_id, "request_id")
        if not isinstance(self.params, Mapping):
            raise ValueError("params must be an object")
        validate_params(self.operation, self.params)
        object.__setattr__(self, "params", ImmutableJsonMapping(self.params))
        encode_message(self.to_dict(), MAX_REQUEST_BYTES)

    def to_dict(self) -> dict:
        return {"protocol_version": self.protocol_version, "request_id": self.request_id,
                "operation": self.operation.value, "params": self.params.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReadRequest":
        if set(value) != {"protocol_version", "request_id", "operation", "params"}:
            raise ValueError("invalid request fields")
        return cls(**value)


@dataclass(frozen=True)
class ReadResponse:
    request_id: str
    operation: ReadOperation
    state: LookupState
    worker_session_id: str
    worker_pid: int | None
    started_monotonic: float
    completed_monotonic: float
    completed_utc_ns: int
    payload: Mapping[str, Any] = field(default_factory=lambda: {"data": None}, repr=False)
    error: str | None = None
    native_error: tuple[int, str] | None = None
    protocol_version: int = READ_PROTOCOL_VERSION

    def __post_init__(self):
        object.__setattr__(self, "operation", ReadOperation(self.operation))
        object.__setattr__(self, "state", LookupState(self.state))
        _identifier(self.request_id, "request_id")
        _identifier(self.worker_session_id, "worker_session_id")
        if type(self.protocol_version) is not int or self.protocol_version != READ_PROTOCOL_VERSION:
            raise ValueError("unsupported read protocol version")
        if self.worker_pid is not None:
            integer(self.worker_pid, "worker_pid", 1, 2**32 - 1)
        finite_number(self.started_monotonic, "started_monotonic", positive=True)
        finite_number(self.completed_monotonic, "completed_monotonic", positive=True)
        if self.completed_monotonic < self.started_monotonic:
            raise ValueError("invalid response clock order")
        integer(self.completed_utc_ns, "completed_utc_ns", 1, 2**63 - 1)
        if not isinstance(self.payload, Mapping) or set(self.payload) != {"data"}:
            raise ValueError("invalid response payload")
        data = self.payload["data"]
        if self.state is LookupState.UNKNOWN:
            if data is not None or not isinstance(self.error, str) or not self.error:
                raise ValueError("unknown must carry reason, never usable data")
        elif self.worker_pid is None or self.error is not None or data is None:
            raise ValueError("known response requires worker and data")
        elif self.state is LookupState.EMPTY:
            if self.operation not in COLLECTION_OPERATIONS or data != []:
                raise ValueError("empty requires a known empty collection")
        elif self.operation in COLLECTION_OPERATIONS:
            if not isinstance(data, list) or not data:
                raise ValueError("found collection must be nonempty")
        if self.native_error is not None:
            code, message = self.native_error
            integer(code, "native error code", -2**31, 2**31 - 1)
            if not isinstance(message, str) or len(message) > 1024:
                raise ValueError("invalid native error message")
            object.__setattr__(self, "native_error", (code, message))
            if self.state is not LookupState.UNKNOWN and code != 1:
                raise ValueError("known result conflicts with native error")
        # Keep nested records immutable without imposing native/numpy types on readers.
        object.__setattr__(self, "payload", ImmutableJsonMapping(self.payload))

    @property
    def value(self) -> Any:
        return self.payload["data"]

    def age_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_monotonic)

    def is_fresh(self, max_age_seconds: float) -> bool:
        finite_number(max_age_seconds, "max_age_seconds", positive=True)
        # Conservative age since query start, including native/guard time. A slow
        # query is not made fresh by its completion. Tick source time is separate.
        return self.state is not LookupState.UNKNOWN and self.age_seconds() <= max_age_seconds

    def to_dict(self) -> dict:
        return {"request_id": self.request_id, "operation": self.operation.value,
                "state": self.state.value, "worker_session_id": self.worker_session_id,
                "worker_pid": self.worker_pid, "started_monotonic": self.started_monotonic,
                "completed_monotonic": self.completed_monotonic,
                "completed_utc_ns": self.completed_utc_ns, "data": self.value,
                "error": self.error, "native_error": self.native_error,
                "protocol_version": self.protocol_version}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReadResponse":
        if set(value) != {"request_id", "operation", "state", "worker_session_id",
                          "worker_pid", "started_monotonic", "completed_monotonic",
                          "completed_utc_ns", "data", "error", "native_error", "protocol_version"}:
            raise ValueError("invalid response fields")
        fields = dict(value)
        fields["payload"] = {"data": fields.pop("data")}
        return cls(**fields)
