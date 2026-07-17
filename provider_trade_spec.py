from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
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
    """Immutable virtual trade input; serialize it explicitly with ``to_dict``."""

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
    evidence_assumptions: tuple[str, ...] = ()

    @property
    def entry_ready(self) -> bool:
        return not self.entry_blockers

    def to_dict(self) -> dict[str, object]:
        """Return detached strict-JSON-safe data without proxy traversal."""
        fields = {
            "provider_signal_id": self.provider_signal_id,
            "channel": self.channel,
            "direction": self.direction,
            "trigger_observed_utc": self.trigger_observed_utc,
            "latency_ms": self.latency_ms,
            "volume_per_leg": self.volume_per_leg,
            "leg_count": self.leg_count,
            "provider_tps": self.provider_tps,
            "provider_sl": self.provider_sl,
            "level_timeline": self.level_timeline,
            "management_events": self.management_events,
            "execution_sig_ids": self.execution_sig_ids,
            "entry_blockers": self.entry_blockers,
            "policy_evidence_gaps": self.policy_evidence_gaps,
            "evidence_assumptions": self.evidence_assumptions,
        }
        return cast(dict[str, object], _thaw_json_safe(fields))


def _thaw_json_safe(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            value = value.astimezone(timezone.utc)
        return value.isoformat()
    if isinstance(value, float) and not isfinite(value):
        if value != value:
            marker = "nan"
        elif value > 0:
            marker = "positive_infinity"
        else:
            marker = "negative_infinity"
        return {"__nonfinite_float__": marker}
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_thaw_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_thaw_json_safe(item) for item in sorted(value, key=repr)]
    if isinstance(value, (bytes, bytearray)):
        return list(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


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


def _freeze_timeline(
    value: object,
    field_name: str,
    invalid_timestamp_gap: str,
) -> tuple[tuple[FrozenEvent, ...], tuple[str, ...]]:
    if value is None:
        return (), ()
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise ValueError(f"{field_name} must be a sequence of mappings")

    try:
        events = list(value)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be a sequence of mappings") from exc
    if not all(isinstance(event, Mapping) for event in events):
        raise ValueError(f"{field_name} must contain only mappings")

    indexed_events = list(enumerate(cast(list[Mapping[str, object]], events)))
    gaps = tuple(
        f"{invalid_timestamp_gap}:{index}"
        for index, event in indexed_events
        if _parse_trigger_utc(event.get("observed_ts_utc"))[0] is None
    )
    indexed_events.sort(key=_causal_sort_key)
    timeline = tuple(
        cast(FrozenEvent, _deep_freeze(event)) for _, event in indexed_events
    )
    return timeline, gaps


def _safe_price(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        price = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not isfinite(price) or price <= 0:
        return None
    return price


def _provider_levels(
    signal: Mapping[str, object],
) -> tuple[tuple[float, ...], float | None, tuple[str, ...]]:
    raw_tps = signal.get("effective_tps")
    if raw_tps is None:
        tp_values: tuple[object, ...] = ()
    elif isinstance(raw_tps, (list, tuple)):
        tp_values = tuple(raw_tps)
    else:
        tp_values = (raw_tps,)

    provider_tps: list[float] = []
    gaps: list[str] = []
    for index, value in enumerate(tp_values):
        price = _safe_price(value)
        if price is None:
            gaps.append(f"invalid_provider_tp:{index}")
        else:
            provider_tps.append(price)

    raw_sl = signal.get("effective_sl")
    provider_sl = None if raw_sl is None else _safe_price(raw_sl)
    if raw_sl is not None and provider_sl is None:
        gaps.append("invalid_provider_sl")
    return tuple(provider_tps), provider_sl, tuple(gaps)


def _runtime_fallback_rows(
    signal: Mapping[str, object],
    *,
    need_tps: bool,
    need_sl: bool,
) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    value = signal.get("runtime_level_timeline")
    if value is None:
        return [], ()
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise ValueError("runtime_level_timeline must be a sequence of mappings")
    try:
        events = list(value)
    except TypeError as exc:
        raise ValueError(
            "runtime_level_timeline must be a sequence of mappings"
        ) from exc
    if not all(isinstance(event, Mapping) for event in events):
        raise ValueError(
            "runtime_level_timeline must contain only mappings"
        )

    rows: list[dict[str, object]] = []
    gaps: list[str] = []
    for event_index, raw_event in enumerate(events):
        event = cast(Mapping[str, object], raw_event)
        selected_tps: list[float] = []
        if need_tps:
            raw_tps = event.get("tps") or ()
            if isinstance(raw_tps, (list, tuple)):
                for tp_index, raw_tp in enumerate(raw_tps):
                    tp = _safe_price(raw_tp)
                    if tp is None:
                        gaps.append(
                            f"invalid_runtime_tp:{event_index}:{tp_index}"
                        )
                    else:
                        selected_tps.append(tp)
            elif raw_tps:
                gaps.append(f"invalid_runtime_tps:{event_index}")

        selected_sl = None
        if need_sl and event.get("sl") is not None:
            selected_sl = _safe_price(event.get("sl"))
            if selected_sl is None:
                gaps.append(f"invalid_runtime_sl:{event_index}")

        if not selected_tps and selected_sl is None:
            continue
        rows.append({
            "observed_ts_utc": event.get("observed_ts_utc"),
            "telegram_ts_utc": event.get("telegram_ts_utc"),
            "tps": selected_tps,
            "sl": selected_sl,
            "provisional": bool(event.get("provisional")),
            "corrections": event.get("corrections") or (),
            "source_kind": (
                event.get("source_kind") or "runtime_entry_interpreter"
            ),
            "source_event": (
                event.get("source_event") or "entry_levels_interpreted"
            ),
        })
    return rows, _stable_unique(gaps)


def _normalize_execution_sig_ids(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError("execution_sig_ids must be a string or sequence of strings")

    execution_sig_ids = tuple(value)
    if not all(isinstance(sig_id, str) for sig_id in execution_sig_ids):
        raise ValueError("execution_sig_ids must contain only strings")
    return execution_sig_ids


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
    contract_status = entry_contract.get("status")
    if contract_status == "blocked" and not entry_blockers:
        entry_blockers = ("contract_status_blocked",)
    elif contract_status not in {"ready", "blocked"}:
        entry_blockers = _stable_unique(
            (*entry_blockers, "invalid_contract_status")
        )

    provider_tps, provider_sl, level_gaps = _provider_levels(signal)
    policy_evidence_gaps = list(level_gaps)
    evidence_assumptions: list[str] = []

    provider_timeline, level_timeline_gaps = _freeze_timeline(
        signal.get("level_timeline"),
        "level_timeline",
        "invalid_level_timeline_observed_ts",
    )
    runtime_rows, runtime_value_gaps = _runtime_fallback_rows(
        signal,
        need_tps=not provider_tps,
        need_sl=provider_sl is None,
    )
    runtime_timeline, runtime_timeline_gaps = _freeze_timeline(
        runtime_rows,
        "runtime_level_timeline",
        "invalid_runtime_level_timeline_observed_ts",
    )

    valid_runtime = [
        event
        for event in runtime_timeline
        if _parse_trigger_utc(event.get("observed_ts_utc"))[0] is not None
    ]
    if not provider_tps:
        for event in reversed(valid_runtime):
            fallback_tps = tuple(
                price
                for raw_price in (event.get("tps") or ())
                if (price := _safe_price(raw_price)) is not None
            )
            if fallback_tps:
                provider_tps = fallback_tps
                evidence_assumptions.append("runtime_inferred_tps_fallback")
                break
    if provider_sl is None:
        for event in reversed(valid_runtime):
            fallback_sl = _safe_price(event.get("sl"))
            if fallback_sl is not None:
                provider_sl = fallback_sl
                evidence_assumptions.append("runtime_inferred_sl_fallback")
                break

    combined_timeline = list(enumerate((
        *provider_timeline,
        *runtime_timeline,
    )))
    combined_timeline.sort(key=_causal_sort_key)
    level_timeline = tuple(event for _, event in combined_timeline)

    if not provider_tps:
        policy_evidence_gaps.append("missing_provider_tps")
    if provider_sl is None:
        policy_evidence_gaps.append("missing_provider_sl")
    management_events, management_event_gaps = _freeze_timeline(
        signal.get("management_events"),
        "management_events",
        "invalid_management_event_observed_ts",
    )
    policy_evidence_gaps.extend(level_timeline_gaps)
    policy_evidence_gaps.extend(runtime_value_gaps)
    policy_evidence_gaps.extend(runtime_timeline_gaps)
    policy_evidence_gaps.extend(management_event_gaps)

    execution_sig_ids = _normalize_execution_sig_ids(
        signal.get("execution_sig_ids")
    )
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
        level_timeline=level_timeline,
        management_events=management_events,
        execution_sig_ids=execution_sig_ids,
        entry_blockers=entry_blockers,
        policy_evidence_gaps=tuple(policy_evidence_gaps),
        evidence_assumptions=_stable_unique(evidence_assumptions),
    )


__all__ = ["ProviderTradeSpec", "build_trade_spec"]
