"""Build a provenance-first inventory of live, native, and shadow evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from execution_latency import valid_versioned_timing


_CLOSURE_EVENTS = {"signal_closed", "pos_summary", "trade_finalized"}
_SIGNAL_ROOT_EVENTS = {
    "signal_received",
    "mt5_action_attempt",
    "mt5_order_result",
    "strategy_shadow_registered",
    "strategy_shadow_recovered",
    "position_opened",
    "position_closed",
    *_CLOSURE_EVENTS,
}
_TIMELINE_PHASES = {
    "signal_received": "receipt",
    "signal_decision": "decision",
    "management_decision": "decision",
    "telegram_understood": "decision",
    "layered_decision": "decision",
    "bot_internal_decision": "decision",
    "interpretation_firewall_decision": "decision",
    "mt5_action_enqueued": "queue",
    "mt5_order_requested": "queue",
    "mt5_modify_requested": "queue",
    "mt5_close_requested": "queue",
    "mt5_order_result": "ack",
    "mt5_action_confirmed": "ack",
    "mt5_close_result": "ack",
    "position_opened": "fill",
    "position_closed": "fill",
    "market_filled": "fill",
    "scale_out_leg_filled": "fill",
    "dca_filled": "fill",
    "positions_closed_by_mt5": "fill",
    "protection_confirmed": "protection",
    "mt5_modify_confirmed": "protection",
    "dubai_broker_sl_confirmed": "protection",
}


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _utc_text(value: Any) -> str | None:
    parsed = _parse_utc(value)
    return parsed.isoformat() if parsed else None


def _source(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    digest = hashlib.sha256()
    size = 0
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return {"path": str(target), "bytes": size, "sha256": digest.hexdigest()}


def _identity(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _assert_unchanged(path: Path, expected: tuple[int, int]) -> None:
    if _identity(path) != expected:
        raise RuntimeError(f"Source changed while inventory was being built: {path}")


def _load_json_source(path: str | Path | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if path is None:
        return {}, None
    target = Path(path)
    before = _identity(target)
    payload = target.read_bytes()
    after = _identity(target)
    if after != before:
        raise RuntimeError(f"Source changed while inventory was being built: {target}")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value, {
        "path": str(target),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _iter_jsonl(path: Path) -> Iterable[tuple[int, Any, str | None]]:
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                yield line_number, json.loads(line), None
            except json.JSONDecodeError as exc:
                yield line_number, None, f"{exc.msg} at column {exc.colno}"


def _channel(signal_id: str, explicit: Any = None) -> str | None:
    if isinstance(explicit, str) and explicit:
        return explicit
    prefix = signal_id.split("_", 1)[0]
    return prefix if prefix.startswith("canal") else None


def _new_signal(signal_id: str, channel: Any = None) -> dict[str, Any]:
    return {
        "signal_id": signal_id,
        "channel": _channel(signal_id, channel),
        "day_utc": None,
        "direction": None,
        "receipt": {
            "observed": False,
            "count": 0,
            "event_utc": None,
            "telegram_utc": None,
        },
        "strategy_contract": None,
        "strategy_contracts": [],
        "contract_gaps": [],
        "day_sources": {
            "event_utc_day": None,
            "telegram_utc_day": None,
            "weekly_shadow_utc_days": [],
            "parity_utc_days": [],
            "native_explicit_utc_days": [],
        },
        "execution": {
            "attempts": 0,
            "sent_attempts": 0,
            "known_not_sent_attempts": 0,
            "unknown_send_attempts": 0,
            "responses_observed": 0,
            "timed_attempts": 0,
            "duration_observed_attempts": 0,
            "legacy_duration_attempts": 0,
            "invalid_timing_attempts": 0,
            "max_duration_ms": None,
            "operations": {},
            "order_results": 0,
            "closures": 0,
        },
        "shadow": {
            "prospective_candidates": [],
            "recovered_candidates": [],
            "unverified_candidates": [],
            "diagnostic_rows": [],
        },
        "native": None,
        "native_rows": [],
        "parity": None,
        "parity_rows": [],
        "quarantine": [],
        "coverage": {"market_ticks": "unknown", "money_fx": "unknown"},
        "sessions": [],
        "event_counts": {},
        "timeline_first_utc": {},
        "gaps": [],
        "_first_event_utc": None,
        "_contract_usage": {},
        "_contract_gap_usage": {},
        "_receipt_candidates": [],
    }


def _duration_ms(event: dict[str, Any]) -> float | None:
    value = event.get("duration_ns")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = float(value) / 1_000_000
        return round(parsed, 6) if math.isfinite(parsed) and parsed >= 0 else None
    value = event.get("duration_ms")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = float(value)
        return round(parsed, 6) if math.isfinite(parsed) and parsed >= 0 else None
    timing = event.get("timing")
    if isinstance(timing, dict):
        value = timing.get("duration_ms")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = float(value)
            return round(parsed, 6) if math.isfinite(parsed) and parsed >= 0 else None
    return None


def _put_day(row: dict[str, Any], source: str, value: Any) -> None:
    if isinstance(value, str) and value and value not in row["day_sources"][source]:
        row["day_sources"][source].append(value)


def _active_contract(
    contracts: dict[str, list[tuple[datetime, dict[str, Any]]]],
    invalidations: dict[str, list[datetime]],
    session: str,
    event_dt: datetime,
) -> tuple[dict[str, Any] | None, bool]:
    selected_time = None
    selected_contract = None
    for contract_dt, contract in reversed(contracts.get(session, [])):
        if contract_dt <= event_dt:
            selected_time = contract_dt
            selected_contract = contract
            break
    conflict_blocks = any(
        invalidation <= event_dt
        and (selected_time is None or invalidation >= selected_time)
        for invalidation in invalidations.get(session, [])
    )
    return (None, True) if conflict_blocks else (selected_contract, False)


def _coverage_by_day(status: dict[str, Any], *, money: bool = False) -> dict[str, str]:
    if not status:
        return {}
    explicit = status.get("coverage_by_day")
    if isinstance(explicit, dict):
        return {
            str(day): str(value.get("status", "unknown")) if isinstance(value, dict) else str(value)
            for day, value in explicit.items()
        }
    required = {str(day) for day in status.get("required_days", [])}
    complete_key = "cached_days" if money else "cached_days"
    complete = {str(day) for day in status.get(complete_key, [])}
    incomplete = {str(day) for day in status.get("incomplete_days", [])}
    invalid = {str(day) for day in status.get("invalid_days", [])}
    missing = {str(day) for day in status.get("missing_days", [])}
    result = {}
    for day in required | complete | incomplete | invalid | missing:
        if day in invalid:
            result[day] = "invalid"
        elif day in incomplete:
            result[day] = "incomplete"
        elif day in missing:
            result[day] = "missing"
        elif day in complete:
            result[day] = "complete"
        else:
            result[day] = "required_unknown"
    return result


def _put_candidate(values: list[str], candidate: Any) -> None:
    if isinstance(candidate, str) and candidate and candidate not in values:
        values.append(candidate)


def _sanitized_contract_metadata(event: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    metadata: dict[str, Any] = {}
    invalid = []
    for name in ("payload_sha256", "code_commit"):
        value = event.get(name)
        if value is None or isinstance(value, str):
            metadata[name] = value
        else:
            metadata[name] = None
            invalid.append(name)
    schema_version = event.get("schema_version")
    if schema_version is None or (
        isinstance(schema_version, (str, int)) and not isinstance(schema_version, bool)
    ):
        metadata["schema_version"] = schema_version
    else:
        metadata["schema_version"] = None
        invalid.append("schema_version")
    metadata["invalid_metadata_fields"] = sorted(invalid)
    return metadata, invalid


def build_inventory(
    *,
    event_paths: Iterable[str | Path],
    weekly_shadow_path: str | Path | None = None,
    weekly_parity_path: str | Path | None = None,
    native_path: str | Path | None = None,
    market_status_path: str | Path | None = None,
    money_status_path: str | Path | None = None,
    since: str,
    until_exclusive: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Return a sanitized inventory without message bodies or invented completeness."""
    since_dt = _parse_utc(since)
    until_dt = _parse_utc(until_exclusive)
    if since_dt is None or until_dt is None or since_dt >= until_dt:
        raise ValueError("since and until_exclusive must be ordered timezone-aware timestamps")

    paths = [Path(path) for path in event_paths]
    weekly_shadow, weekly_shadow_source = _load_json_source(weekly_shadow_path)
    parity, parity_source = _load_json_source(weekly_parity_path)
    native, native_source = _load_json_source(native_path)
    market_status, market_source = _load_json_source(market_status_path)
    money_status, money_source = _load_json_source(money_status_path)

    signals: dict[str, dict[str, Any]] = {}
    roots: set[str] = set()
    contracts: dict[str, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    event_sources: list[dict[str, Any]] = []
    event_identities: list[tuple[int, int]] = []
    event_id_fingerprints: dict[str, str] = {}
    event_id_occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)
    conflicting_ids: set[str] = set()
    quarantines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    contract_invalidations: dict[str, list[datetime]] = defaultdict(list)
    contract_ambiguities: list[dict[str, Any]] = []
    malformed = 0
    non_objects = 0
    invalid_timestamps = 0
    total_lines = 0
    duplicate_event_ids = 0
    conflicting_event_ids = 0
    invalid_contract_metadata_fields = 0
    auxiliary_unplaced: list[dict[str, Any]] = []
    auxiliary_excluded: list[dict[str, Any]] = []

    def get_signal(signal_id: str, channel: Any = None) -> dict[str, Any]:
        row = signals.setdefault(signal_id, _new_signal(signal_id, channel))
        if row["channel"] is None:
            row["channel"] = _channel(signal_id, channel)
        return row

    def classify_day(value: Any) -> tuple[str | None, str]:
        if not isinstance(value, str) or len(value) != 10:
            return None, "unplaced"
        try:
            start = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        except ValueError:
            return None, "unplaced"
        if start >= until_dt or start + timedelta(days=1) <= since_dt:
            return value, "outside_scope"
        return value, "in_scope"

    def record_auxiliary_problem(
        target: list[dict[str, Any]],
        *,
        source: str,
        signal_id: Any,
        reason: str,
        day_utc: str | None = None,
    ) -> None:
        item = {"source": source, "signal_id": signal_id, "reason": reason}
        if day_utc is not None:
            item["day_utc"] = day_utc
        target.append(item)

    # First pass discovers the complete signal universe and session-local contracts.
    for path in paths:
        identity = _identity(path)
        source = _source(path)
        _assert_unchanged(path, identity)
        event_sources.append(source)
        event_identities.append(identity)
        for _line_number, event, error in _iter_jsonl(path):
            total_lines += 1
            if error:
                malformed += 1
                continue
            if not isinstance(event, dict):
                non_objects += 1
                continue
            event_name = event.get("ev")
            event_dt = _parse_utc(event.get("ts"))
            event_id = event.get("event_id")
            if isinstance(event_id, str) and event_id:
                fingerprint = hashlib.sha256(
                    json.dumps(event, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()
                event_id_occurrences[event_id].append(
                    {
                        "path": str(path),
                        "line_number": _line_number,
                        "sha256": fingerprint,
                        "event_name": str(event_name or "unknown"),
                        "event_utc": event_dt.isoformat() if event_dt else None,
                        "signal_id": event.get("sig"),
                        "session_id": event.get("session_id"),
                    }
                )
                previous = event_id_fingerprints.get(event_id)
                if previous is None:
                    event_id_fingerprints[event_id] = fingerprint
                elif previous != fingerprint:
                    if event_id not in conflicting_ids:
                        conflicting_event_ids += 1
                    conflicting_ids.add(event_id)
                else:
                    duplicate_event_ids += 1
            if event_name == "live_strategy_contract":
                if event_dt is None:
                    invalid_timestamps += 1
                    continue
                session = event.get("session_id")
                if not isinstance(session, str) or not session:
                    continue
                metadata, invalid_metadata = _sanitized_contract_metadata(event)
                invalid_contract_metadata_fields += len(invalid_metadata)
                contracts[session].append(
                    (
                        event_dt,
                        {
                            "event_utc": event_dt.isoformat(),
                            "session_id": session,
                            **metadata,
                            "_event_id": event_id,
                        },
                    )
                )
                continue
            signal_id = event.get("sig")
            if not isinstance(signal_id, str) or not signal_id.startswith("canal"):
                continue
            if event_dt is None:
                invalid_timestamps += 1
                continue
            if since_dt <= event_dt < until_dt and event_name in _SIGNAL_ROOT_EVENTS:
                roots.add(signal_id)
        _assert_unchanged(path, identity)

    for event_id in sorted(conflicting_ids):
        occurrences = event_id_occurrences[event_id]
        for occurrence in occurrences:
            signal_id = occurrence.get("signal_id")
            occurrence_dt = _parse_utc(occurrence.get("event_utc"))
            if (
                isinstance(signal_id, str)
                and signal_id.startswith("canal")
                and occurrence.get("event_name") in _SIGNAL_ROOT_EVENTS
                and occurrence_dt is not None
                and since_dt <= occurrence_dt < until_dt
            ):
                roots.add(signal_id)
            if occurrence.get("event_name") == "live_strategy_contract":
                session = occurrence.get("session_id")
                invalidation = _parse_utc(occurrence.get("event_utc"))
                if isinstance(session, str) and session and invalidation is not None:
                    contract_invalidations[session].append(invalidation)

    for session, values in contracts.items():
        filtered = [
            (contract_dt, contract)
            for contract_dt, contract in values
            if contract.get("_event_id") not in conflicting_ids
        ]
        by_time: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
        for contract_dt, contract in filtered:
            by_time[contract_dt].append(contract)
        resolved: list[tuple[datetime, dict[str, Any]]] = []
        for contract_dt, same_time in by_time.items():
            identities = {
                json.dumps(
                    {
                        key: value
                        for key, value in contract.items()
                        if key != "_event_id"
                    },
                    sort_keys=True,
                    default=str,
                )
                for contract in same_time
            }
            if len(identities) > 1:
                contract_invalidations[session].append(contract_dt)
                contract_ambiguities.append(
                    {
                        "session_id": session,
                        "event_utc": contract_dt.isoformat(),
                        "contracts": len(identities),
                    }
                )
                continue
            resolved.append(
                (
                    contract_dt,
                    min(same_time, key=lambda item: str(item.get("_event_id") or "")),
                )
            )
        contracts[session] = sorted(resolved, key=lambda item: item[0])
        contract_invalidations[session] = sorted(set(contract_invalidations[session]))

    weekly_rows: list[tuple[dict[str, Any], str, int]] = []
    for source_row_index, item in enumerate(weekly_shadow.get("rows", [])):
        if not isinstance(item, dict) or not isinstance(item.get("signal_id"), str):
            continue
        day, status = classify_day(item.get("day"))
        if status == "unplaced":
            record_auxiliary_problem(
                auxiliary_unplaced,
                source="weekly_shadow",
                signal_id=item["signal_id"],
                reason="missing_or_invalid_utc_day",
            )
            continue
        if status == "outside_scope":
            record_auxiliary_problem(
                auxiliary_excluded,
                source="weekly_shadow",
                signal_id=item["signal_id"],
                reason="outside_scope",
                day_utc=day,
            )
            continue
        weekly_rows.append((item, day, source_row_index))
        roots.add(item["signal_id"])

    parity_rows: list[tuple[str, dict[str, Any], str, int]] = []
    for cohort_name in ("historical_complete_matrix", "current_week_partial"):
        cohort = parity.get(cohort_name, {})
        details = cohort.get("details", []) if isinstance(cohort, dict) else []
        for source_row_index, item in enumerate(details):
            if not isinstance(item, dict) or not isinstance(item.get("signal_id"), str):
                continue
            day, status = classify_day(item.get("day"))
            if status == "unplaced":
                record_auxiliary_problem(
                    auxiliary_unplaced,
                    source="weekly_parity",
                    signal_id=item["signal_id"],
                    reason="missing_or_invalid_utc_day",
                )
                continue
            if status == "outside_scope":
                record_auxiliary_problem(
                    auxiliary_excluded,
                    source="weekly_parity",
                    signal_id=item["signal_id"],
                    reason="outside_scope",
                    day_utc=day,
                )
                continue
            parity_rows.append((cohort_name, item, day, source_row_index))
            roots.add(item["signal_id"])

    native_candidates: list[tuple[dict[str, Any], str | None, str, int]] = []
    for source_row_index, item in enumerate(native.get("baskets", [])):
        if not isinstance(item, dict) or not isinstance(item.get("signal_id"), str):
            continue
        signal_id = item["signal_id"]
        explicit_day, day_status = classify_day(item.get("day_utc"))
        native_candidates.append((item, explicit_day, day_status, source_row_index))
        if day_status == "in_scope":
            roots.add(signal_id)

    native_rows: list[tuple[dict[str, Any], str | None, str, int]] = []
    for item, explicit_day, day_status, source_row_index in native_candidates:
        signal_id = item["signal_id"]
        if day_status == "outside_scope":
            record_auxiliary_problem(
                auxiliary_excluded,
                source="native",
                signal_id=signal_id,
                reason="outside_scope",
                day_utc=explicit_day,
            )
            continue
        if day_status == "in_scope":
            native_rows.append((item, explicit_day, day_status, source_row_index))
            continue
        if signal_id in roots:
            native_rows.append((item, explicit_day, day_status, source_row_index))
        else:
            record_auxiliary_problem(
                auxiliary_unplaced,
                source="native",
                signal_id=signal_id,
                reason="native_calendar_not_utc",
            )

    for event_id in sorted(conflicting_ids):
        occurrences = event_id_occurrences[event_id]
        for signal_id in sorted(
            {
                occurrence.get("signal_id")
                for occurrence in occurrences
                if isinstance(occurrence.get("signal_id"), str)
                and occurrence.get("signal_id") in roots
                and (occurrence_dt := _parse_utc(occurrence.get("event_utc"))) is not None
                and since_dt <= occurrence_dt < until_dt
            }
        ):
            quarantines[signal_id].append(
                {
                    "reason": "conflicting_event_id",
                    "event_id": event_id,
                    "occurrences": len(occurrences),
                    "source_occurrences": [
                        {
                            key: item.get(key)
                            for key in (
                                "path",
                                "line_number",
                                "sha256",
                                "event_name",
                                "event_utc",
                            )
                        }
                        for item in occurrences
                        if item.get("signal_id") == signal_id
                    ],
                }
            )

    for signal_id, items in quarantines.items():
        row = get_signal(signal_id)
        row["quarantine"].extend(items)
        row["gaps"].append("conflicting_event_identity_quarantined")

    def set_first_phase(row: dict[str, Any], phase: str, value: datetime) -> None:
        current = _parse_utc(row["timeline_first_utc"].get(phase))
        if current is None or value < current:
            row["timeline_first_utc"][phase] = value.isoformat()

    processed_event_ids: set[str] = set()

    # Second pass retains events that occurred before the first root event in file order.
    for path, identity in zip(paths, event_identities):
        for _line_number, event, error in _iter_jsonl(path):
            if error or not isinstance(event, dict):
                continue
            event_id = event.get("event_id")
            if isinstance(event_id, str) and event_id:
                if event_id in conflicting_ids or event_id in processed_event_ids:
                    continue
                processed_event_ids.add(event_id)
            signal_id = event.get("sig")
            if not isinstance(signal_id, str) or signal_id not in roots:
                continue
            event_dt = _parse_utc(event.get("ts"))
            if event_dt is None or not since_dt <= event_dt < until_dt:
                continue
            event_name = event.get("ev")
            row = get_signal(signal_id)
            event_utc = event_dt.isoformat()
            first_event = _parse_utc(row["_first_event_utc"])
            if first_event is None or event_dt < first_event:
                row["_first_event_utc"] = event_utc
            event_counts = Counter(row["event_counts"])
            event_counts[str(event_name or "unknown")] += 1
            row["event_counts"] = dict(sorted(event_counts.items()))
            session = event.get("session_id")
            contract = None
            contract_blocked = False
            if isinstance(session, str) and session and session not in row["sessions"]:
                row["sessions"].append(session)
            if isinstance(session, str) and session:
                session_first = row.setdefault("_session_first_utc", {})
                previous = _parse_utc(session_first.get(session))
                if previous is None or event_dt < previous:
                    session_first[session] = event_utc
                contract, contract_blocked = _active_contract(
                    contracts,
                    contract_invalidations,
                    session,
                    event_dt,
                )
                if contract is None:
                    gap = row["_contract_gap_usage"].setdefault(
                        session,
                        {
                            "session_id": session,
                            "first_event_utc": event_utc,
                            "event_count": 0,
                        },
                    )
                    if event_dt < _parse_utc(gap["first_event_utc"]):
                        gap["first_event_utc"] = event_utc
                    gap["event_count"] += 1
                    if contract_blocked:
                        row["gaps"].append(
                            "strategy_contract_conflict_prevents_attribution"
                        )
                else:
                    contract_key = (session, contract["event_utc"])
                    usage = row["_contract_usage"].setdefault(
                        contract_key,
                        {
                            **{key: value for key, value in contract.items() if not key.startswith("_")},
                            "first_linked_event_utc": event_utc,
                            "last_linked_event_utc": event_utc,
                            "event_count": 0,
                        },
                    )
                    if event_dt < _parse_utc(usage["first_linked_event_utc"]):
                        usage["first_linked_event_utc"] = event_utc
                    if event_dt > _parse_utc(usage["last_linked_event_utc"]):
                        usage["last_linked_event_utc"] = event_utc
                    usage["event_count"] += 1
            else:
                gap = row["_contract_gap_usage"].setdefault(
                    "__missing_session__",
                    {
                        "session_id": None,
                        "first_event_utc": event_utc,
                        "event_count": 0,
                    },
                )
                if event_dt < _parse_utc(gap["first_event_utc"]):
                    gap["first_event_utc"] = event_utc
                gap["event_count"] += 1
                row["gaps"].append("event_session_id_not_observed")
            phase = _TIMELINE_PHASES.get(str(event_name))
            if phase:
                set_first_phase(row, phase, event_dt)

            if event_name == "signal_received":
                receipt = row["receipt"]
                receipt["observed"] = True
                receipt["count"] += 1
                row["_receipt_candidates"].append(
                    {
                        "event_utc": event_utc,
                        "telegram_utc": _utc_text(event.get("tg_ts")),
                        "direction": event.get("direction"),
                        "channel": _channel(signal_id, event.get("channel")),
                        "session_id": session if isinstance(session, str) and session else None,
                        "contract_key": (
                            (session, contract["event_utc"])
                            if isinstance(session, str) and session and contract is not None
                            else None
                        ),
                        "event_id": event_id if isinstance(event_id, str) else None,
                        "path": str(path),
                        "line_number": _line_number,
                    }
                )
            elif event_name == "mt5_action_attempt":
                execution = row["execution"]
                execution["attempts"] += 1
                operation = str(event.get("operation") or "unknown")
                operations = Counter(execution["operations"])
                operations[operation] += 1
                execution["operations"] = dict(sorted(operations.items()))
                duration = _duration_ms(event)
                timing_schema = event.get("timing_schema_version")
                timing_valid = timing_schema == 1 and valid_versioned_timing(event)
                if timing_valid:
                    execution["timed_attempts"] += 1
                elif timing_schema is not None or event.get("attempt_timing_complete") is True:
                    execution["invalid_timing_attempts"] += 1
                if duration is not None:
                    execution["duration_observed_attempts"] += 1
                    if timing_schema is None:
                        execution["legacy_duration_attempts"] += 1
                    previous = execution["max_duration_ms"]
                    execution["max_duration_ms"] = duration if previous is None else max(previous, duration)
                sent = event.get("broker_request_sent")
                if sent is True:
                    execution["sent_attempts"] += 1
                    started = _parse_utc(event.get("broker_request_started_utc"))
                    received = _parse_utc(event.get("broker_response_received_utc"))
                    if started is not None:
                        set_first_phase(row, "send", started)
                    else:
                        row["gaps"].append("sent_attempt_missing_valid_broker_start")
                    if received is not None and started is not None and received >= started:
                        execution["responses_observed"] += 1
                        set_first_phase(row, "broker_response", received)
                    elif event.get("broker_response_received_utc") is not None:
                        row["gaps"].append("invalid_broker_response_boundary")
                elif sent is False:
                    execution["known_not_sent_attempts"] += 1
                else:
                    execution["unknown_send_attempts"] += 1
            elif event_name == "mt5_order_result":
                row["execution"]["order_results"] += 1
            elif event_name in _CLOSURE_EVENTS:
                row["execution"]["closures"] += 1
            elif event_name == "strategy_shadow_registered":
                _put_candidate(row["shadow"]["prospective_candidates"], event.get("candidate_id"))
            elif event_name == "strategy_shadow_recovered":
                candidate = event.get("candidate_id")
                if not candidate and isinstance(event.get("state"), dict):
                    candidate = event["state"].get("candidate_id")
                _put_candidate(row["shadow"]["recovered_candidates"], candidate)
        _assert_unchanged(path, identity)

    for item, day_utc, source_row_index in weekly_rows:
        row = get_signal(item["signal_id"], item.get("channel"))
        candidate = item.get("candidate_id")
        if item.get("registration_observed") is True:
            _put_candidate(row["shadow"]["prospective_candidates"], candidate)
        else:
            _put_candidate(row["shadow"]["unverified_candidates"], candidate)
        row["shadow"]["diagnostic_rows"].append(
            {
                "candidate_id": candidate,
                "day_utc": day_utc,
                "source_row_index": source_row_index,
                "status": item.get("status"),
                "usable": item.get("usable"),
                "registration_observed": item.get("registration_observed") is True,
                "net_eur": item.get("net_eur"),
                "blockers": item.get("blockers", []),
            }
        )
        _put_day(row, "weekly_shadow_utc_days", day_utc)

    for cohort_name, item, day_utc, source_row_index in parity_rows:
        row = get_signal(item["signal_id"], item.get("channel"))
        row["parity_rows"].append({
            "cohort": cohort_name,
            "day_utc": day_utc,
            "source_row_index": source_row_index,
            **{
                key: item.get(key)
                for key in (
                    "candidate_id",
                    "shadow_entries",
                    "actual_entries",
                    "entry_match",
                    "shadow_net_eur",
                    "actual_net_eur",
                    "money_match_cent",
                    "money_delta_eur",
                    "joint_match",
                )
            },
        })
        _put_day(row, "parity_utc_days", item["day"])

    native_calendar = native.get("calendar")
    for item, explicit_day, day_status, source_row_index in native_rows:
        signal_id = item["signal_id"]
        row = get_signal(signal_id, item.get("channel"))
        row["native_rows"].append({
            "day_utc": explicit_day,
            "source_row_index": source_row_index,
            "positions": item.get("positions"),
            "net_eur": item.get("net_eur"),
            "first_native_calendar": item.get("first_native_calendar"),
            "last_native_calendar": item.get("last_native_calendar"),
            "calendar_claim": native_calendar,
        })
        if day_status == "in_scope":
            _put_day(row, "native_explicit_utc_days", explicit_day)

    market = _coverage_by_day(market_status)
    money = _coverage_by_day(money_status, money=True)
    ordered = sorted(signals.values(), key=lambda row: (row["_first_event_utc"] or "", row["signal_id"]))
    for row in ordered:
        candidates = row["_receipt_candidates"]
        if candidates:
            earliest_utc = min(candidate["event_utc"] for candidate in candidates)
            earliest = [
                candidate for candidate in candidates if candidate["event_utc"] == earliest_utc
            ]
            semantics = {
                (
                    candidate["telegram_utc"],
                    candidate["direction"],
                    candidate["channel"],
                    candidate["session_id"],
                    candidate["contract_key"],
                )
                for candidate in earliest
            }
            row["receipt"]["event_utc"] = earliest_utc
            if len(semantics) == 1:
                selected = min(
                    earliest,
                    key=lambda item: (
                        item["event_id"] or "",
                        item["path"],
                        item["line_number"],
                    ),
                )
                row["receipt"]["telegram_utc"] = selected["telegram_utc"]
                row["direction"] = selected["direction"]
                row["channel"] = selected["channel"]
                if selected["contract_key"] is not None:
                    row["_receipt_contract_key"] = selected["contract_key"]
            else:
                row["channel"] = None
                row["gaps"].append("ambiguous_earliest_signal_receipt")
                row["quarantine"].append(
                    {
                        "reason": "ambiguous_earliest_signal_receipt",
                        "event_utc": earliest_utc,
                        "occurrences": len(earliest),
                        "source_occurrences": [
                            {
                                "path": candidate["path"],
                                "line_number": candidate["line_number"],
                                "event_id": candidate["event_id"],
                                "telegram_utc": candidate["telegram_utc"],
                                "direction": candidate["direction"],
                                "channel": candidate["channel"],
                                "session_id": candidate["session_id"],
                            }
                            for candidate in sorted(
                                earliest,
                                key=lambda item: (
                                    item["event_id"] or "",
                                    item["path"],
                                    item["line_number"],
                                ),
                            )
                        ],
                    }
                )
        if len(row["parity_rows"]) == 1:
            row["parity"] = row["parity_rows"][0]
        elif len(row["parity_rows"]) > 1:
            row["gaps"].append("multiple_parity_rows_observed")
        if len(row["native_rows"]) == 1:
            row["native"] = row["native_rows"][0]
        elif len(row["native_rows"]) > 1:
            row["gaps"].append("multiple_native_baskets_observed")
        row["strategy_contracts"] = sorted(
            row["_contract_usage"].values(),
            key=lambda item: (item["first_linked_event_utc"], item["session_id"]),
        )
        row["contract_gaps"] = sorted(
            row["_contract_gap_usage"].values(),
            key=lambda item: (item["first_event_utc"], item["session_id"] or ""),
        )
        receipt_contract_key = row.get("_receipt_contract_key")
        if receipt_contract_key in row["_contract_usage"]:
            row["strategy_contract"] = {
                key: value
                for key, value in row["_contract_usage"][receipt_contract_key].items()
                if key not in {"first_linked_event_utc", "last_linked_event_utc", "event_count"}
            }
        elif not row["receipt"]["observed"] and row["strategy_contracts"]:
            row["strategy_contract"] = {
                key: value
                for key, value in row["strategy_contracts"][0].items()
                if key not in {"first_linked_event_utc", "last_linked_event_utc", "event_count"}
            }

        event_basis = row["receipt"]["event_utc"] or row["_first_event_utc"]
        event_dt = _parse_utc(event_basis)
        event_day = event_dt.date().isoformat() if event_dt else None
        telegram_dt = _parse_utc(row["receipt"]["telegram_utc"])
        telegram_day = telegram_dt.date().isoformat() if telegram_dt else None
        row["day_sources"]["event_utc_day"] = event_day
        row["day_sources"]["telegram_utc_day"] = telegram_day
        auxiliary_days = sorted(
            {
                day
                for source in (
                    "weekly_shadow_utc_days",
                    "parity_utc_days",
                    "native_explicit_utc_days",
                )
                for day in row["day_sources"][source]
            }
        )
        if event_day is not None:
            row["day_utc"] = event_day
            if any(day != event_day for day in auxiliary_days):
                row["gaps"].append("auxiliary_day_conflicts_with_event_day")
        elif len(auxiliary_days) == 1:
            row["day_utc"] = auxiliary_days[0]
        elif len(auxiliary_days) > 1:
            row["gaps"].append("auxiliary_utc_days_conflict")
        day = row["day_utc"]
        row["coverage"] = {
            "market_ticks": market.get(day, "unknown"),
            "money_fx": money.get(day, "unknown"),
        }
        if not row["receipt"]["observed"]:
            row["gaps"].append("signal_receipt_not_observed_in_event_sources")
        if row["strategy_contract"] is None:
            row["gaps"].append("preceding_strategy_contract_not_observed")
        if row["contract_gaps"]:
            row["gaps"].append("strategy_contract_not_observed_for_one_or_more_sessions")
        if row["coverage"]["market_ticks"] != "complete":
            row["gaps"].append("market_tick_coverage_not_complete")
        if row["coverage"]["money_fx"] != "complete":
            row["gaps"].append("money_fx_coverage_not_complete")
        row["shadow"]["prospective_candidates"].sort()
        row["shadow"]["recovered_candidates"].sort()
        row["shadow"]["unverified_candidates"].sort()
        row["sessions"].sort()
        row["gaps"] = sorted(set(row["gaps"]))
        row.pop("_first_event_utc", None)
        row.pop("_receipt_session", None)
        row.pop("_session_first_utc", None)
        row.pop("_receipt_contract_key", None)
        row.pop("_contract_usage", None)
        row.pop("_contract_gap_usage", None)
        row.pop("_receipt_candidates", None)

    ordered.sort(key=lambda row: (row["day_utc"] or "", row["channel"] or "", row["signal_id"]))
    daily_groups: dict[tuple[str | None, str | None], list[dict[str, Any]]] = defaultdict(list)
    for row in ordered:
        daily_groups[(row["day_utc"], row["channel"])].append(row)
    daily = []
    for (day, channel), rows in sorted(daily_groups.items(), key=lambda item: (item[0][0] or "", item[0][1] or "")):
        daily.append(
            {
                "day_utc": day,
                "channel": channel,
                "signals": len(rows),
                "receipts_observed": sum(row["receipt"]["observed"] for row in rows),
                "native_baskets": sum(bool(row["native_rows"]) for row in rows),
                "prospective_shadow_signals": sum(bool(row["shadow"]["prospective_candidates"]) for row in rows),
                "recovered_only_shadow_signals": sum(
                    bool(row["shadow"]["recovered_candidates"])
                    and not row["shadow"]["prospective_candidates"]
                    for row in rows
                ),
                "market_ticks": market.get(day, "unknown"),
                "money_fx": money.get(day, "unknown"),
            }
        )

    source_files = list(event_sources)
    source_files.extend(
        source
        for source in (weekly_shadow_source, parity_source, native_source, market_source, money_source)
        if source is not None
    )
    generated = _utc_text(generated_at or datetime.now(timezone.utc).isoformat())
    if generated is None:
        raise ValueError("generated_at must be timezone-aware")
    conflicting_event_details = [
        {
            "event_id": event_id,
            "occurrences": sorted(
                event_id_occurrences[event_id],
                key=lambda item: (item["path"], item["line_number"]),
            ),
        }
        for event_id in sorted(conflicting_ids)
    ]
    return {
        "schema_version": 6,
        "generated_at_utc": generated,
        "scope": {"since_utc": since_dt.isoformat(), "until_exclusive_utc": until_dt.isoformat()},
        "source_contract": "sanitized_runtime_evidence_inventory_not_a_completeness_or_parity_claim",
        "generator": {
            "module": str(Path(__file__)),
            "sha256": _source(__file__)["sha256"],
        },
        "sources": source_files,
        "source_quality": {
            "event_lines": total_lines,
            "malformed_event_lines": malformed,
            "non_object_event_lines": non_objects,
            "invalid_event_timestamps": invalid_timestamps,
            "invalid_contract_metadata_fields": invalid_contract_metadata_fields,
            "duplicate_event_ids": duplicate_event_ids,
            "conflicting_event_ids": conflicting_event_ids,
            "excluded_conflicting_event_ids": len(conflicting_ids),
            "conflicting_event_details": conflicting_event_details,
            "contract_ambiguities": sorted(
                contract_ambiguities,
                key=lambda item: (item["event_utc"], item["session_id"]),
            ),
        },
        "auxiliary_quality": {
            "excluded_outside_scope": auxiliary_excluded,
            "unplaced": auxiliary_unplaced,
        },
        "signals": ordered,
        "daily": daily,
    }
