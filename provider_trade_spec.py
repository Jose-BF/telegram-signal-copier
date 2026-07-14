from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from numbers import Integral, Real
from types import MappingProxyType
from typing import cast


FrozenEvent = Mapping[str, object]
_MAX_UTC = datetime.max.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ProviderTradeSpec:
    provider_signal_id: str
    channel: str
    direction: str | None
    trigger_observed_utc: datetime | None
    latency_ms: int
    volume_per_leg: float
    leg_count: int
    provider_tps: tuple[float, ...]
    provider_sl: float | None
    level_timeline: tuple[FrozenEvent, ...]
    management_events: tuple[FrozenEvent, ...]
    execution_sig_ids: tuple[str, ...]
    entry_blockers: tuple[str, ...]
    policy_evidence_gaps: tuple[str, ...]

    @property
    def entry_ready(self) -> bool:
        return not self.entry_blockers


def _validate_latency(latency_ms: object) -> int:
    if (
        isinstance(latency_ms, bool)
        or not isinstance(latency_ms, Integral)
        or latency_ms < 0
    ):
        raise ValueError("latency_ms must be an integer greater than or equal to 0")
    return int(latency_ms)


def _validate_volume(volume_per_leg: object) -> float:
    if isinstance(volume_per_leg, bool) or not isinstance(volume_per_leg, Real):
        raise ValueError("volume_per_leg must be a positive finite number")
    volume = float(volume_per_leg)
    if not isfinite(volume) or volume <= 0:
        raise ValueError("volume_per_leg must be a positive finite number")
    return volume


def _parse_trigger_utc(value: object) -> tuple[datetime | None, str | None]:
    if value is None or value == "":
        return None, "missing_trigger_observed_utc"

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None, "invalid_trigger_observed_utc"
    else:
        return None, "invalid_trigger_observed_utc"

    try:
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None, "invalid_trigger_observed_utc"
        return parsed.astimezone(timezone.utc), None
    except (OverflowError, ValueError):
        return None, "invalid_trigger_observed_utc"


def _stable_unique(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        blocker = str(value)
        if blocker not in seen:
            result.append(blocker)
            seen.add(blocker)
    return tuple(result)


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        frozen = {key: _deep_freeze(item) for key, item in value.items()}
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_deep_freeze(item) for item in value)
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def _causal_sort_key(
    indexed_event: tuple[int, Mapping[str, object]],
) -> tuple[bool, datetime, int]:
    source_index, event = indexed_event
    observed, _ = _parse_trigger_utc(event.get("observed_ts_utc"))
    return observed is None, observed or _MAX_UTC, source_index


def _freeze_timeline(value: object, field_name: str) -> tuple[FrozenEvent, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise ValueError(f"{field_name} must be a sequence of mappings")

    try:
        events = list(value)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be a sequence of mappings") from exc
    if not all(isinstance(event, Mapping) for event in events):
        raise ValueError(f"{field_name} must contain only mappings")

    indexed_events = list(enumerate(cast(list[Mapping[str, object]], events)))
    indexed_events.sort(key=_causal_sort_key)
    return tuple(
        cast(FrozenEvent, _deep_freeze(event))
        for _, event in indexed_events
    )


def _provider_levels(signal: Mapping[str, object]) -> tuple[tuple[float, ...], float | None]:
    raw_tps = signal.get("effective_tps") or ()
    if isinstance(raw_tps, (str, bytes, bytearray, Mapping)):
        raise ValueError("effective_tps must be a sequence of numeric values")
    try:
        provider_tps = tuple(float(value) for value in raw_tps)
    except (TypeError, ValueError) as exc:
        raise ValueError("effective_tps must be a sequence of numeric values") from exc

    raw_sl = signal.get("effective_sl")
    if raw_sl is None:
        provider_sl = None
    else:
        try:
            provider_sl = float(raw_sl)
        except (TypeError, ValueError) as exc:
            raise ValueError("effective_sl must be numeric or None") from exc
    return provider_tps, provider_sl


def build_trade_spec(
    signal: Mapping[str, object],
    latency_ms: int = 0,
    volume_per_leg: float = 0.01,
) -> ProviderTradeSpec:
    if signal.get("record_type") != "formal_signal":
        raise ValueError("build_trade_spec accepts only formal_signal records")

    latency = _validate_latency(latency_ms)
    volume = _validate_volume(volume_per_leg)

    raw_entry_contract = signal.get("entry_contract")
    entry_contract: Mapping[str, object] = (
        raw_entry_contract if isinstance(raw_entry_contract, Mapping) else {}
    )
    raw_direction = entry_contract.get("direction")
    direction = raw_direction if isinstance(raw_direction, str) else None
    added_blockers: list[str] = []
    if not direction:
        added_blockers.append("missing_direction")
    elif direction not in {"BUY", "SELL"}:
        added_blockers.append("invalid_direction")

    trigger_observed_utc, timestamp_blocker = _parse_trigger_utc(
        entry_contract.get("trigger_observed_utc")
    )
    if timestamp_blocker is not None:
        added_blockers.append(timestamp_blocker)

    raw_blockers = entry_contract.get("blockers") or ()
    if isinstance(raw_blockers, str):
        inherited_blockers: Iterable[object] = (raw_blockers,)
    else:
        try:
            inherited_blockers = tuple(raw_blockers)
        except TypeError:
            inherited_blockers = (raw_blockers,)
    entry_blockers = _stable_unique((*inherited_blockers, *added_blockers))

    provider_tps, provider_sl = _provider_levels(signal)
    policy_evidence_gaps: list[str] = []
    if not provider_tps:
        policy_evidence_gaps.append("missing_provider_tps")
    if provider_sl is None:
        policy_evidence_gaps.append("missing_provider_sl")

    execution_sig_ids = tuple(signal.get("execution_sig_ids") or ())
    return ProviderTradeSpec(
        provider_signal_id=str(signal.get("provider_signal_id") or ""),
        channel=str(signal.get("channel") or ""),
        direction=direction,
        trigger_observed_utc=trigger_observed_utc,
        latency_ms=latency,
        volume_per_leg=volume,
        leg_count=len(provider_tps) or 1,
        provider_tps=provider_tps,
        provider_sl=provider_sl,
        level_timeline=_freeze_timeline(
            signal.get("level_timeline"), "level_timeline"
        ),
        management_events=_freeze_timeline(
            signal.get("management_events"), "management_events"
        ),
        execution_sig_ids=execution_sig_ids,
        entry_blockers=entry_blockers,
        policy_evidence_gaps=tuple(policy_evidence_gaps),
    )


__all__ = ["ProviderTradeSpec", "build_trade_spec"]
