"""Verified conversion between MT5 server epochs and UTC tick time."""

from __future__ import annotations

from datetime import datetime, timezone
from numbers import Integral


OFFSET_QUANTUM_SECONDS = 15 * 60
MAX_OFFSET_SECONDS = 14 * 60 * 60
INFERENCE_RESIDUAL_TOLERANCE_SECONDS = 30.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_utc(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _validated_offset(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, Integral)
        or abs(int(value)) > MAX_OFFSET_SECONDS
        or int(value) % OFFSET_QUANTUM_SECONDS != 0
    ):
        raise ValueError("invalid broker UTC offset evidence")
    return int(value)


def contract_utc_offset_seconds(
    contract: dict,
    *,
    at_utc: datetime,
) -> int:
    """Return the latest verified broker offset known at ``at_utc``."""
    target = _as_utc(at_utc)
    candidates: list[tuple[datetime, int]] = []
    for snapshot in contract.get("swap_snapshots") or ():
        if not isinstance(snapshot, dict):
            continue
        captured = _parse_utc(snapshot.get("captured_at_utc"))
        if captured is None or captured > target:
            continue
        try:
            offset = _validated_offset(
                (snapshot.get("time_evidence") or {}).get(
                    "utc_offset_seconds"
                )
            )
        except ValueError:
            continue
        candidates.append((captured, offset))
    if not candidates:
        raise ValueError("broker UTC offset evidence unavailable")
    return max(candidates, key=lambda row: row[0])[1]


def inferred_utc_offset_seconds(
    raw_server_msc: int,
    *,
    observed_utc: datetime,
) -> int:
    """Infer a quarter-hour server offset from a freshly observed live tick."""
    raw = int(raw_server_msc)
    if raw <= 0:
        raise ValueError("invalid broker tick time")
    observed = _as_utc(observed_utc)
    raw_utc = datetime.fromtimestamp(raw / 1000.0, tz=timezone.utc)
    delta_seconds = (raw_utc - observed).total_seconds()
    offset = int(
        round(delta_seconds / OFFSET_QUANTUM_SECONDS)
        * OFFSET_QUANTUM_SECONDS
    )
    residual = delta_seconds - offset
    _validated_offset(offset)
    if abs(residual) > INFERENCE_RESIDUAL_TOLERANCE_SECONDS:
        raise ValueError("broker tick clock could not be aligned to UTC")
    return offset


def resolve_utc_offset_seconds(
    *,
    contract: dict | None,
    raw_server_msc: int,
    observed_utc: datetime,
) -> int:
    """Resolve and cross-check durable and live broker-clock evidence."""
    durable = None
    inferred = None
    if isinstance(contract, dict):
        try:
            durable = contract_utc_offset_seconds(
                contract,
                at_utc=observed_utc,
            )
        except ValueError:
            durable = None
    try:
        inferred = inferred_utc_offset_seconds(
            raw_server_msc,
            observed_utc=observed_utc,
        )
    except ValueError:
        inferred = None
    if durable is not None and inferred is not None and durable != inferred:
        raise ValueError("broker UTC offset evidence mismatch")
    if durable is not None:
        return durable
    if inferred is not None:
        return inferred
    raise ValueError("broker UTC offset could not be proven")


def normalize_server_msc(raw_server_msc: int, offset_seconds: int) -> int:
    return int(raw_server_msc) - _validated_offset(offset_seconds) * 1000


def server_query_datetime(utc_msc: int, offset_seconds: int) -> datetime:
    server_msc = int(utc_msc) + _validated_offset(offset_seconds) * 1000
    return datetime.fromtimestamp(server_msc / 1000.0, tz=timezone.utc)
