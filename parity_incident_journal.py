"""Portable append-only history for strategy-shadow repair incidents."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from strategy_shadow_contracts import canonical_hash


_EVENT = "strategy_shadow_parity_incident"


def _registry_from_incidents(
    incidents: list[dict[str, Any]],
    *,
    observation_id: str,
) -> dict[str, Any]:
    ordered = sorted(
        incidents,
        key=lambda row: (
            row.get("status") == "resolved",
            str(row.get("channel") or ""),
            str(row.get("signal_id") or ""),
            str(row.get("blocker") or ""),
            str(row.get("incident_id") or ""),
        ),
    )
    open_items = [row for row in ordered if row.get("status") != "resolved"]
    comparison_open = [
        row for row in open_items if row.get("blocks_comparison") is True
    ]
    return {
        "schema_version": 1,
        "observation_id": observation_id,
        "open_count": len(open_items),
        "comparison_blocking_open_count": len(comparison_open),
        "regressed_count": sum(
            row.get("status") == "regressed" for row in ordered
        ),
        "resolved_count": sum(
            row.get("status") == "resolved" for row in ordered
        ),
        "ranking_blocked": bool(open_items),
        "comparison_blocked": bool(comparison_open),
        "unresolved_incident_ids": [
            str(row["incident_id"]) for row in open_items
        ],
        "incidents": ordered,
    }


def _validated_registry(value: Mapping[str, Any]) -> dict[str, Any]:
    registry = dict(value)
    if registry.get("schema_version") != 1:
        raise ValueError("invalid incident journal registry schema")
    observation_id = str(registry.get("observation_id") or "")
    raw_incidents = registry.get("incidents")
    if not observation_id or not isinstance(raw_incidents, list):
        raise ValueError("invalid incident journal registry")
    incidents: list[dict[str, Any]] = []
    identities: set[str] = set()
    for raw in raw_incidents:
        if not isinstance(raw, Mapping):
            raise ValueError("invalid incident journal row")
        incident = dict(raw)
        incident_id = str(incident.get("incident_id") or "")
        if (
            not incident_id
            or incident_id in identities
            or incident.get("status") not in {"open", "resolved", "regressed"}
        ):
            raise ValueError("invalid incident journal identity or status")
        identities.add(incident_id)
        incidents.append(incident)
    rebuilt = _registry_from_incidents(
        incidents,
        observation_id=observation_id,
    )
    if registry != rebuilt:
        raise ValueError("incident journal registry totals are inconsistent")
    return rebuilt


def _event_for(
    incident: Mapping[str, Any],
    *,
    observation_id: str,
) -> dict[str, Any]:
    semantic = {
        "ev": _EVENT,
        "observation_id": observation_id,
        "incident_id": str(incident["incident_id"]),
        "incident": dict(incident),
    }
    digest = canonical_hash(semantic)
    return {
        **semantic,
        "schema_version": 1,
        "event_id": f"parity_event_{digest}",
        "payload_sha256": digest,
    }


def _read_events(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    if not path.is_file():
        return [], set()
    events: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"invalid incident journal: {path}") from exc
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid incident journal: {path}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"invalid incident journal: {path}")
        semantic = {
            "ev": event.get("ev"),
            "observation_id": event.get("observation_id"),
            "incident_id": event.get("incident_id"),
            "incident": event.get("incident"),
        }
        digest = canonical_hash(semantic)
        event_id = str(event.get("event_id") or "")
        incident = event.get("incident")
        if (
            event.get("schema_version") != 1
            or event.get("ev") != _EVENT
            or event.get("payload_sha256") != digest
            or event_id != f"parity_event_{digest}"
            or not isinstance(incident, Mapping)
            or str(incident.get("incident_id") or "")
            != str(event.get("incident_id") or "")
        ):
            raise ValueError(f"invalid incident journal: {path}")
        if event_id in event_ids:
            continue
        event_ids.add(event_id)
        events.append(event)
    return events, event_ids


def load_registry(path: Path) -> dict[str, Any] | None:
    """Rebuild the latest incident state from the portable event stream."""

    events, _event_ids = _read_events(Path(path))
    if not events:
        return None
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        latest[str(event["incident_id"])] = dict(event["incident"])
    return _registry_from_incidents(
        list(latest.values()),
        observation_id=str(events[-1]["observation_id"]),
    )


def append_registry(path: Path, registry: Mapping[str, Any]) -> int:
    """Append only changed incident states and return the number added."""

    path = Path(path)
    normalized = _validated_registry(registry)
    _events, existing_ids = _read_events(path)
    additions = []
    for incident in normalized["incidents"]:
        event = _event_for(
            incident,
            observation_id=normalized["observation_id"],
        )
        if event["event_id"] not in existing_ids:
            additions.append(event)
    if not additions:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_bytes() if path.is_file() else b""
    if existing and not existing.endswith(b"\n"):
        raise ValueError(f"invalid incident journal: {path}")
    suffix = "".join(
        json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        for event in additions
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(existing)
            handle.write(suffix)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return len(additions)
