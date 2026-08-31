"""Structural parity between a live MT5 basket and its shadow control."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from strategy_shadow_contracts import (
    ShadowPolicy,
    ShadowPosition,
    ShadowSignalState,
    canonical_hash,
    normalize_direction,
)


_LEG_RE = re.compile(r"_(?:B|D)(\d+)(?:_|$)", re.IGNORECASE)


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if math.isfinite(normalized) else None


def _rounded(value: object) -> float | None:
    normalized = _finite(value)
    return None if normalized is None else round(normalized, 6)


def _exit_class(reason: object) -> str | None:
    normalized = str(reason or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"tp", "target"}:
        return "target"
    if normalized in {"sl", "be", "break_even", "protective_stop"}:
        return "protective_stop"
    return "managed_close"


def _target_contract(
    *,
    direction: str,
    entry_price: object,
    target_price: object,
    policy: ShadowPolicy,
) -> dict[str, Any] | None:
    entry = _finite(entry_price)
    target = _finite(target_price)
    if entry is None or target is None or target <= 0.0:
        return None
    if policy.target_steps:
        sign = 1.0 if normalize_direction(direction) == "BUY" else -1.0
        return {
            "mode": "relative_xau",
            "value": round((target - entry) * sign, 6),
        }
    return {"mode": "absolute_xau", "value": round(target, 6)}


def _protection_contract(
    *,
    direction: str,
    entry_price: object,
    stop_price: object,
) -> dict[str, Any] | None:
    entry = _finite(entry_price)
    stop = _finite(stop_price)
    if entry is None or stop is None or stop <= 0.0:
        return None
    sign = 1.0 if normalize_direction(direction) == "BUY" else -1.0
    return {
        "mode": "relative_xau",
        "value": round((stop - entry) * sign, 6),
    }


def _latest_confirmed_level(
    position: Mapping[str, Any], field: str
) -> float | None:
    history = position.get(f"{field}_history")
    if not isinstance(history, list):
        return None
    observed = None
    for item in history:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "") not in {"confirmed", "snapshot"}:
            continue
        value = _finite(item.get(field))
        if value is not None and value > 0.0:
            observed = value
    return observed


def _position_signature(
    *,
    leg_index: int,
    volume: object,
    entry_price: object,
    target_price: object,
    stop_price: object,
    closed: bool,
    close_reason: object,
    direction: str,
    policy: ShadowPolicy,
) -> dict[str, Any]:
    protection = _protection_contract(
        direction=direction,
        entry_price=entry_price,
        stop_price=stop_price,
    )
    return {
        "leg_index": int(leg_index),
        "volume": _rounded(volume),
        "target": _target_contract(
            direction=direction,
            entry_price=entry_price,
            target_price=target_price,
            policy=policy,
        ),
        "protection_set": protection is not None,
        "protection": protection,
        "status": "closed" if closed else "open",
        "exit_class": _exit_class(close_reason) if closed else None,
    }


def shadow_logic_signature(
    state: ShadowSignalState,
    policy: ShadowPolicy,
) -> dict[str, Any]:
    positions = [
        _position_signature(
            leg_index=position.leg_index,
            volume=position.volume,
            entry_price=position.entry_price,
            target_price=position.target_price,
            stop_price=position.stop_price,
            closed=position.status == "closed",
            close_reason=position.close_reason,
            direction=state.direction,
            policy=policy,
        )
        for position in sorted(state.positions, key=lambda item: item.leg_index)
    ]
    return {
        "schema_version": 2,
        "strategy_id": state.candidate_id,
        "strategy_fingerprint": state.strategy_fingerprint,
        "direction": normalize_direction(state.direction),
        "status": state.status,
        "positions": positions,
    }


def _actual_leg_index(position: Mapping[str, Any]) -> int | None:
    if str(position.get("role") or "") == "market_a":
        return 0
    open_deal = position.get("open_deal")
    comment = (
        str(open_deal.get("comment") or "")
        if isinstance(open_deal, Mapping)
        else ""
    )
    match = _LEG_RE.search(comment)
    return None if match is None else int(match.group(1))


def actual_logic_signature(
    source: Mapping[str, Any],
    policy: ShadowPolicy,
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    direction = str(source.get("direction") or "")
    try:
        direction = normalize_direction(direction)
    except ValueError:
        return None, ("actual_direction_missing",)
    raw_positions = source.get("positions")
    if not isinstance(raw_positions, list):
        return None, ("actual_positions_missing",)

    indexed: list[tuple[int, Mapping[str, Any]]] = []
    observed_indexes: set[int] = set()
    for position in raw_positions:
        if not isinstance(position, Mapping):
            return None, ("actual_position_invalid",)
        leg_index = _actual_leg_index(position)
        if leg_index is None or leg_index in observed_indexes:
            return None, ("actual_leg_identity_ambiguous",)
        observed_indexes.add(leg_index)
        indexed.append((leg_index, position))

    snapshot = source.get("strategy_snapshot")
    if not isinstance(snapshot, Mapping):
        return None, ("actual_strategy_snapshot_missing",)
    strategy_id = str(snapshot.get("live_strategy_id") or "")
    strategy_fingerprint = str(
        snapshot.get("live_strategy_fingerprint") or ""
    )
    if not strategy_id or not strategy_fingerprint:
        return None, ("actual_strategy_identity_missing",)

    positions = []
    for leg_index, position in sorted(indexed):
        open_deal = position.get("open_deal")
        open_deal_volume = (
            open_deal.get("volume") if isinstance(open_deal, Mapping) else None
        )
        positions.append(_position_signature(
            leg_index=leg_index,
            volume=(
                position.get("volume")
                if position.get("volume") is not None
                else open_deal_volume
            ),
            entry_price=position.get("open_price"),
            target_price=_latest_confirmed_level(position, "tp"),
            stop_price=_latest_confirmed_level(position, "sl"),
            closed=position.get("is_closed") is True,
            close_reason=position.get("close_reason"),
            direction=direction,
            policy=policy,
        ))
    raw_status = str(source.get("status") or "open").strip().lower()
    if positions and all(item["status"] == "closed" for item in positions):
        status = "closed"
    elif not positions and raw_status in {
        "cancelled",
        "entry_expired",
        "expired",
        "no_position",
    }:
        status = "cancelled"
    else:
        status = raw_status
    return ({
        "schema_version": 2,
        "strategy_id": strategy_id,
        "strategy_fingerprint": strategy_fingerprint,
        "direction": direction,
        "status": status,
        "positions": positions,
    }, ())


def _differences(actual: Any, shadow: Any, path: str = "") -> list[str]:
    if isinstance(actual, Mapping) and isinstance(shadow, Mapping):
        output: list[str] = []
        for key in sorted(set(actual) | set(shadow)):
            child = f"{path}.{key}" if path else str(key)
            if key not in actual or key not in shadow:
                output.append(child)
                continue
            output.extend(_differences(actual[key], shadow[key], child))
        return output
    if isinstance(actual, list) and isinstance(shadow, list):
        output = []
        if len(actual) != len(shadow):
            output.append(f"{path}.length")
        for index, (left, right) in enumerate(zip(actual, shadow)):
            output.extend(_differences(left, right, f"{path}[{index}]"))
        return output
    return [] if actual == shadow else [path]


def compare_logic_signatures(
    actual: Mapping[str, Any] | None,
    shadow: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if actual is None or shadow is None:
        return {
            "match": False,
            "actual_signature_hash": None,
            "shadow_signature_hash": None,
            "differences": ["signature_missing"],
        }
    differences = _differences(actual, shadow)
    return {
        "match": not differences,
        "actual_signature_hash": canonical_hash(actual),
        "shadow_signature_hash": canonical_hash(shadow),
        "differences": differences[:50],
    }
