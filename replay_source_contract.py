"""Bind a replay artifact to the exact ledger and event stream that built it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1


def default_manifest_path(replay_path: Path) -> Path:
    return Path(f"{Path(replay_path)}.manifest.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    path = Path(path)
    return {
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _jsonl_row_count(path: Path) -> int:
    with Path(path).open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def write_manifest(
    *,
    replay_path: Path,
    ledger_path: Path,
    events_path: Path,
    row_count: int,
    manifest_path: Path | None = None,
) -> Path:
    replay_path = Path(replay_path)
    ledger_path = Path(ledger_path)
    events_path = Path(events_path)
    manifest_path = (
        Path(manifest_path)
        if manifest_path is not None
        else default_manifest_path(replay_path)
    )
    for role, path in (
        ("replay", replay_path),
        ("ledger", ledger_path),
        ("trade_events", events_path),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"missing_{role}:{path}")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise ValueError("row_count must be a non-negative integer")
    actual_rows = _jsonl_row_count(replay_path)
    if actual_rows != row_count:
        raise ValueError(
            f"replay row count mismatch: expected {row_count}, got {actual_rows}"
        )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "replay": {
            **_file_record(replay_path),
            "row_count": row_count,
        },
        "sources": {
            "ledger": _file_record(ledger_path),
            "trade_events": _file_record(events_path),
        },
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_name(f".{manifest_path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest_path


def _record_matches(path: Path, record: Any) -> bool:
    if not isinstance(record, Mapping) or not Path(path).is_file():
        return False
    try:
        expected_size = int(record["size_bytes"])
        expected_sha256 = str(record["sha256"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        expected_size == Path(path).stat().st_size
        and len(expected_sha256) == 64
        and sha256_file(Path(path)) == expected_sha256
    )


def validate_manifest(
    *,
    replay_path: Path,
    ledger_path: Path,
    events_path: Path,
    manifest_path: Path | None = None,
) -> list[str]:
    replay_path = Path(replay_path)
    ledger_path = Path(ledger_path)
    events_path = Path(events_path)
    manifest_path = (
        Path(manifest_path)
        if manifest_path is not None
        else default_manifest_path(replay_path)
    )
    if not manifest_path.is_file():
        return ["missing_replay_source_manifest"]
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ["invalid_replay_source_manifest"]
    if not isinstance(payload, Mapping) or payload.get("schema_version") != SCHEMA_VERSION:
        return ["invalid_replay_source_manifest"]

    replay_record = payload.get("replay")
    sources = payload.get("sources")
    if not isinstance(replay_record, Mapping) or not isinstance(sources, Mapping):
        return ["invalid_replay_source_manifest"]

    errors: list[str] = []
    if not _record_matches(replay_path, replay_record):
        errors.append("replay_changed")
    else:
        try:
            expected_rows = int(replay_record["row_count"])
        except (KeyError, TypeError, ValueError):
            errors.append("invalid_replay_source_manifest")
        else:
            if expected_rows != _jsonl_row_count(replay_path):
                errors.append("replay_row_count_changed")
    if not _record_matches(ledger_path, sources.get("ledger")):
        errors.append("source_changed:ledger")
    if not _record_matches(events_path, sources.get("trade_events")):
        errors.append("source_changed:trade_events")
    return errors
