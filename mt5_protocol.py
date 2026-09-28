"""Pure protocol types for the isolated MT5 worker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from collections.abc import Iterator
from typing import Any, Mapping
import uuid


PROTOCOL_VERSION = 1


class ImmutableJsonMapping(Mapping[str, Any]):
    """Deeply immutable JSON object backed by its canonical representation."""

    def __init__(self, value: Mapping[str, Any]):
        self._json = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            try:
                return self.to_dict() == dict(other)
            except (TypeError, ValueError):
                return False
        return False


class IntentState(str, Enum):
    PREPARED = "PREPARED"
    DISPATCHING = "DISPATCHING"
    PLACED = "PLACED"
    DONE_PARTIAL = "DONE_PARTIAL"
    DONE = "DONE"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class LookupState(str, Enum):
    FOUND = "FOUND"
    EMPTY = "EMPTY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class IntentKey:
    account_fingerprint: str
    channel: str
    signal_root: str
    generation: int
    leg: str
    operation: str
    revision: int

    def to_dict(self) -> dict[str, Any]:
        values = {
            "account_fingerprint": self.account_fingerprint,
            "channel": self.channel,
            "signal_root": self.signal_root,
            "generation": self.generation,
            "leg": self.leg,
            "operation": self.operation,
            "revision": self.revision,
        }
        for name in ("account_fingerprint", "channel", "signal_root", "leg", "operation"):
            if not isinstance(values[name], str) or not values[name]:
                raise ValueError(f"{name} must be a non-empty string")
        for name in ("generation", "revision"):
            if not isinstance(values[name], int) or isinstance(values[name], bool) or values[name] < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        return values

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IntentKey":
        key = cls(
            account_fingerprint=value.get("account_fingerprint"),
            channel=value.get("channel"),
            signal_root=value.get("signal_root"),
            generation=value.get("generation"),
            leg=value.get("leg"),
            operation=value.get("operation"),
            revision=value.get("revision"),
        )
        key.to_dict()
        return key

    @property
    def intent_id(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        return f"mt5i-v{PROTOCOL_VERSION}-{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class BrokerRequest:
    request_id: str
    intent_id: str
    attempt_id: str
    action_id: str
    intent_key: IntentKey
    payload: Mapping[str, Any]
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        if not isinstance(self.payload, ImmutableJsonMapping):
            object.__setattr__(self, "payload", ImmutableJsonMapping(self.payload))

    @classmethod
    def create(cls, intent_key: IntentKey, payload: Mapping[str, Any], **ids: str) -> "BrokerRequest":
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        return cls(
            request_id=ids.get("request_id") or uuid.uuid4().hex,
            intent_id=intent_key.intent_id,
            attempt_id=ids.get("attempt_id") or uuid.uuid4().hex,
            action_id=ids.get("action_id") or uuid.uuid4().hex,
            intent_key=intent_key,
            payload=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version: {self.protocol_version}")
        if self.intent_id != self.intent_key.intent_id:
            raise ValueError("intent_id does not match intent_key")
        for name in ("request_id", "attempt_id", "action_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} must be a non-empty string")
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "intent_id": self.intent_id,
            "attempt_id": self.attempt_id,
            "action_id": self.action_id,
            "intent_key": self.intent_key.to_dict(),
            "payload": _mapping_dict(self.payload),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BrokerRequest":
        if value.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version: {value.get('protocol_version')}")
        key_value = value.get("intent_key")
        payload = value.get("payload")
        if not isinstance(key_value, Mapping) or not isinstance(payload, Mapping):
            raise ValueError("request must include mapping intent_key and payload")
        request = cls(
            protocol_version=PROTOCOL_VERSION,
            request_id=value.get("request_id"),
            intent_id=value.get("intent_id"),
            attempt_id=value.get("attempt_id"),
            action_id=value.get("action_id"),
            intent_key=IntentKey.from_dict(key_value),
            payload=payload,
        )
        request.to_dict()
        return request


@dataclass(frozen=True)
class BrokerOutcome:
    state: IntentState
    retcode: int | None = None
    order: int | None = None
    deal: int | None = None
    filled_volume: float | None = None
    price: float | None = None
    error: str | None = None
    raw_result: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.raw_result is not None and not isinstance(self.raw_result, ImmutableJsonMapping):
            object.__setattr__(self, "raw_result", ImmutableJsonMapping(self.raw_result))

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "retcode": self.retcode,
            "order": self.order,
            "deal": self.deal,
            "filled_volume": self.filled_volume,
            "price": self.price,
            "error": self.error,
            "raw_result": _mapping_dict(self.raw_result) if self.raw_result is not None else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BrokerOutcome":
        try:
            state = IntentState(value.get("state"))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid broker outcome state") from exc
        raw_result = value.get("raw_result")
        if raw_result is not None and not isinstance(raw_result, Mapping):
            raise ValueError("raw_result must be a mapping or null")
        return cls(
            state=state,
            retcode=_optional_int(value.get("retcode")),
            order=_optional_int(value.get("order")),
            deal=_optional_int(value.get("deal")),
            filled_volume=_optional_float(value.get("filled_volume")),
            price=_optional_float(value.get("price")),
            error=str(value.get("error")) if value.get("error") is not None else None,
            raw_result=raw_result,
        )


_KNOWN_REJECTION_RETCODES = {
    10004,
    10006,
    10007,
    10013,
    10014,
    10015,
    10016,
    10017,
    10018,
    10019,
    10020,
    10021,
    10022,
    10024,
    10026,
    10027,
    10028,
    10029,
    10030,
    10031,
    10032,
    10033,
    10034,
    10035,
    10036,
    10038,
    10039,
    10040,
    10041,
    10042,
    10043,
    10044,
    10045,
    10046,
}


def classify_broker_result(result: Mapping[str, Any] | None) -> BrokerOutcome:
    if result is None:
        return BrokerOutcome(IntentState.UNKNOWN, error="broker_result_missing")
    if not isinstance(result, Mapping):
        return BrokerOutcome(IntentState.UNKNOWN, error="broker_result_not_mapping")
    try:
        raw_result = ImmutableJsonMapping(result)
    except (TypeError, ValueError, OverflowError):
        return BrokerOutcome(IntentState.UNKNOWN, error="broker_result_not_json_serializable")
    normalized = raw_result.to_dict()
    retcode = normalized.get("retcode")
    if not isinstance(retcode, int) or isinstance(retcode, bool):
        return BrokerOutcome(
            IntentState.UNKNOWN,
            error="broker_retcode_missing",
            raw_result=raw_result,
        )
    if retcode in {10009, 10025}:
        state = IntentState.DONE
    elif retcode == 10008:
        state = IntentState.PLACED
    elif retcode == 10010:
        state = IntentState.DONE_PARTIAL
    elif retcode in {10011, 10012}:
        state = IntentState.UNKNOWN
    elif retcode in _KNOWN_REJECTION_RETCODES:
        state = IntentState.REJECTED
    else:
        state = IntentState.UNKNOWN
    return BrokerOutcome(
        state=state,
        retcode=retcode,
        order=_optional_int(normalized.get("order")),
        deal=_optional_int(normalized.get("deal")),
        filled_volume=_optional_float(
            normalized.get("volume")
            if normalized.get("volume") is not None
            else normalized.get("filled_volume")
        ),
        price=_optional_float(normalized.get("price")),
        error=(
            str(normalized.get("comment"))
            if normalized.get("comment") is not None
            else ("unrecognized_broker_retcode" if state is IntentState.UNKNOWN else None)
        ),
        raw_result=raw_result,
    )


def classify_lookup_result(result: Any) -> LookupState:
    if result is None:
        return LookupState.UNKNOWN
    if isinstance(result, (list, tuple)) and not result:
        return LookupState.EMPTY
    return LookupState.FOUND


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(value: Any) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ):
        return float(value)
    return None


def _mapping_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, ImmutableJsonMapping):
        return value.to_dict()
    return ImmutableJsonMapping(value).to_dict()
