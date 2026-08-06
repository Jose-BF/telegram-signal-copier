from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from numbers import Real
from types import MappingProxyType
from typing import Any


FrozenRow = Mapping[str, object]


@dataclass(frozen=True)
class ZoneState:
    observed_utc: datetime
    direction: str
    zone: tuple[float, float]
    tps: tuple[float, ...]
    sl: float


@dataclass(frozen=True)
class ProviderZoneSpec:
    provider_signal_id: str
    channel: str
    ready_at_utc: datetime | None
    ready_states: tuple[ZoneState, ...]
    management_events: tuple[FrozenRow, ...]
    execution_batches: tuple[FrozenRow, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    source_sha256: str

    @property
    def entry_ready(self) -> bool:
        return self.ready_at_utc is not None and not self.blockers


def _stable_strings(values: Iterable[object]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


def _parse_utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _safe_price(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        price = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return price if isfinite(price) and price > 0 else None


def _zone(value: object) -> tuple[float, float] | None:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 2
    ):
        return None
    first = _safe_price(value[0])
    second = _safe_price(value[1])
    if first is None or second is None:
        return None
    return min(first, second), max(first, second)


def _tps(value: object, direction: str | None) -> tuple[float, ...] | None:
    if value is None:
        return None
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        raw_values = tuple(value)
    else:
        raw_values = (value,)
    if not raw_values:
        return None
    prices = tuple(_safe_price(item) for item in raw_values)
    if any(price is None for price in prices):
        return None
    unique = tuple(dict.fromkeys(float(price) for price in prices if price))
    if not unique:
        return None
    return tuple(sorted(unique, reverse=direction == "SELL"))


def _geometry_blocker(
    direction: str,
    zone: tuple[float, float],
    tps: tuple[float, ...],
    sl: float,
) -> str | None:
    lower, upper = zone
    if direction == "BUY":
        if not sl < lower <= upper or any(tp <= upper for tp in tps):
            return "invalid_buy_zone_geometry"
        return None
    if direction == "SELL":
        if not all(tp < lower for tp in tps) or not lower <= upper < sl:
            return "invalid_sell_zone_geometry"
        return None
    return "invalid_zone_direction"


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _deep_freeze(item) for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_deep_freeze(item) for item in value)
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def _json_safe(value: object) -> object:
    if isinstance(value, datetime):
        parsed = _parse_utc(value)
        return parsed.isoformat() if parsed is not None else str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=repr)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _source_sha256(record: Mapping[str, object]) -> str:
    encoded = json.dumps(
        _json_safe(record),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rows(value: object, field_name: str) -> list[Mapping[str, object]]:
    if value is None:
        return []
    if isinstance(value, Mapping) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field_name} must be a sequence of mappings")
    try:
        rows = list(value)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be a sequence of mappings") from exc
    if not all(isinstance(row, Mapping) for row in rows):
        raise ValueError(f"{field_name} must contain only mappings")
    return rows


def _frozen_sorted_rows(
    value: object,
    field_name: str,
    blockers: list[str],
) -> tuple[FrozenRow, ...]:
    ordered: list[tuple[datetime, int, Mapping[str, object]]] = []
    for index, row in enumerate(_rows(value, field_name)):
        observed = _parse_utc(row.get("observed_ts_utc"))
        if observed is None:
            blockers.append(f"invalid_{field_name}_observed_ts:{index}")
            continue
        ordered.append((observed, index, row))
    ordered.sort(key=lambda item: (item[0], item[1]))
    return tuple(
        _deep_freeze(row)  # type: ignore[arg-type]
        for _observed, _index, row in ordered
    )


def _frozen_execution_batches(
    value: object,
    blockers: list[str],
) -> tuple[FrozenRow, ...]:
    ordered: list[tuple[datetime, int, Mapping[str, object]]] = []
    for index, row in enumerate(_rows(value, "execution_batches")):
        observed = _parse_utc(
            row.get("signal_received_utc") or row.get("first_fill_utc")
        )
        if observed is None:
            blockers.append(f"invalid_execution_batch_time:{index}")
            continue
        ordered.append((observed, index, row))
    ordered.sort(key=lambda item: (item[0], item[1]))
    return tuple(
        _deep_freeze(row)  # type: ignore[arg-type]
        for _observed, _index, row in ordered
    )


def build_zone_trade_spec(record: Mapping[str, object]) -> ProviderZoneSpec:
    if record.get("record_type") != "zone_plan":
        raise ValueError("build_zone_trade_spec accepts only zone_plan records")

    source_sha256 = _source_sha256(record)
    blockers: list[str] = []
    warnings: list[str] = []
    updates: list[tuple[datetime, int, str, object]] = []
    source_order = 0

    def append_update(
        raw_observed: object,
        kind: str,
        value: object,
        invalid_label: str,
    ) -> None:
        nonlocal source_order
        observed = _parse_utc(raw_observed)
        if observed is None:
            blockers.append(f"{invalid_label}:{source_order}")
        else:
            updates.append((observed, source_order, kind, value))
        source_order += 1

    for row in _rows(record.get("zone_plan_timeline"), "zone_plan_timeline"):
        if row.get("direction") is not None:
            append_update(
                row.get("observed_ts_utc"),
                "direction",
                row.get("direction"),
                "invalid_zone_direction_observed_ts",
            )
    for row in _rows(record.get("revisions"), "revisions"):
        parsed = row.get("parsed")
        if isinstance(parsed, Mapping) and parsed.get("direction") is not None:
            append_update(
                row.get("observed_ts_utc"),
                "direction",
                parsed.get("direction"),
                "invalid_revision_direction_observed_ts",
            )
    for row in _rows(record.get("entry_zone_timeline"), "entry_zone_timeline"):
        append_update(
            row.get("observed_ts_utc"),
            "zone",
            row.get("range"),
            "invalid_zone_range_observed_ts",
        )
    for field_name in ("level_timeline", "runtime_level_timeline"):
        for row in _rows(record.get(field_name), field_name):
            append_update(
                row.get("observed_ts_utc"),
                "levels",
                {"tps": row.get("tps"), "sl": row.get("sl")},
                f"invalid_{field_name}_observed_ts",
            )

    updates.sort(key=lambda item: (item[0], item[1]))
    direction: str | None = None
    active_zone: tuple[float, float] | None = None
    active_tps: tuple[float, ...] = ()
    active_sl: float | None = None
    states: list[ZoneState] = []
    invalid_geometries: list[str] = []

    for observed, _order, kind, value in updates:
        if kind == "direction":
            candidate = str(value or "").upper()
            direction = candidate if candidate in {"BUY", "SELL"} else None
        elif kind == "zone":
            candidate_zone = _zone(value)
            if candidate_zone is None:
                warnings.append(f"invalid_zone_range:{observed.isoformat()}")
            else:
                active_zone = candidate_zone
        else:
            level_values = value if isinstance(value, Mapping) else {}
            if level_values.get("tps"):
                candidate_tps = _tps(level_values.get("tps"), direction)
                if candidate_tps is None:
                    warnings.append(
                        f"invalid_provider_tps:{observed.isoformat()}"
                    )
                else:
                    active_tps = candidate_tps
            if level_values.get("sl") is not None:
                candidate_sl = _safe_price(level_values.get("sl"))
                if candidate_sl is None:
                    warnings.append(
                        f"invalid_provider_sl:{observed.isoformat()}"
                    )
                else:
                    active_sl = candidate_sl

        if (
            direction is None
            or active_zone is None
            or not active_tps
            or active_sl is None
        ):
            continue
        ordered_tps = tuple(sorted(active_tps, reverse=direction == "SELL"))
        geometry = _geometry_blocker(
            direction,
            active_zone,
            ordered_tps,
            active_sl,
        )
        if geometry is not None:
            invalid_geometries.append(geometry)
            if states:
                warnings.append(f"{geometry}:{observed.isoformat()}")
            continue
        state = ZoneState(
            observed_utc=observed,
            direction=direction,
            zone=active_zone,
            tps=ordered_tps,
            sl=active_sl,
        )
        if not states or state != states[-1]:
            states.append(state)

    if not states:
        if direction is None:
            blockers.append("missing_causal_direction")
        if active_zone is None:
            blockers.append("missing_causal_zone_range")
        if not active_tps:
            blockers.append("missing_causal_provider_tps")
        if active_sl is None:
            blockers.append("missing_causal_provider_sl")
        if (
            direction is not None
            and active_zone is not None
            and active_tps
            and active_sl is not None
            and invalid_geometries
        ):
            blockers.append(invalid_geometries[-1])

    management_events = _frozen_sorted_rows(
        record.get("management_events"),
        "management_events",
        blockers,
    )
    execution_batches = (
        _frozen_execution_batches(record.get("execution_batches"), blockers)
        if record.get("execution_batches")
        else ()
    )
    return ProviderZoneSpec(
        provider_signal_id=str(record.get("provider_signal_id") or ""),
        channel=str(record.get("channel") or ""),
        ready_at_utc=states[0].observed_utc if states else None,
        ready_states=tuple(states),
        management_events=management_events,
        execution_batches=execution_batches,
        blockers=_stable_strings(blockers),
        warnings=_stable_strings(warnings),
        source_sha256=source_sha256,
    )


__all__ = ["ProviderZoneSpec", "ZoneState", "build_zone_trade_spec"]
