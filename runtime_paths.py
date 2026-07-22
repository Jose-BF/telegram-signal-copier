"""Path and migration contract for production runtime evidence.

The Git-tracked ``data`` directory is a historical seed. Live processes write
to an ignored runtime directory so normal operation never dirties the code
checkout.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parent
LEGACY_DATA_DIR_NAME = "data"
DEFAULT_RUNTIME_DATA_DIR_NAME = "runtime_data"
RUNTIME_MANIFEST_NAME = ".runtime-store.json"
MATERIALIZE_MARKER_NAME = ".runtime-materialize-in-progress"
AUTHORITATIVE_STREAMS = (
    "trade_events.jsonl",
    "trade_journal.csv",
    "trade_events_TEST.jsonl",
    "trade_journal_TEST.csv",
)


@dataclass(frozen=True)
class MigrationResult:
    ok: bool
    runtime_dir: Path
    copied: tuple[str, ...]
    preserved: tuple[str, ...]
    archived_tails: tuple[str, ...]
    manifest_path: Path


def _repo_root(repo: Path | None) -> Path:
    return Path(repo or ROOT).resolve()


def _configured_runtime_dir(
    repo: Path,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    values = os.environ if environ is None else environ
    raw = str(values.get("BOT_RUNTIME_DATA_DIR", "")).strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def default_runtime_data_dir(repo: Path | None = None) -> Path:
    return _repo_root(repo) / DEFAULT_RUNTIME_DATA_DIR_NAME


def legacy_data_dir(repo: Path | None = None) -> Path:
    return _repo_root(repo) / LEGACY_DATA_DIR_NAME


def active_data_dir(
    repo: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the authoritative data directory for this process.

    An explicit environment value is authoritative even before migration so a
    launcher can establish the storage boundary before importing other modules.
    Without it, an initialized local runtime store wins; an unchanged checkout
    falls back to the historical tracked seed.
    """

    root = _repo_root(repo)
    configured = _configured_runtime_dir(root, environ)
    if configured is not None:
        return configured
    runtime = default_runtime_data_dir(root)
    if (
        (runtime / RUNTIME_MANIFEST_NAME).is_file()
        and not (runtime / MATERIALIZE_MARKER_NAME).exists()
    ):
        return runtime
    return legacy_data_dir(root)


def data_path(
    name: str,
    *,
    repo: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    candidate = Path(name)
    if candidate.name != name or name in {"", ".", ".."}:
        raise ValueError("runtime data path must be a simple filename")
    return active_data_dir(repo, environ=environ) / name


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _complete_jsonl_prefix(payload: bytes) -> tuple[bytes, bytes]:
    if not payload or payload.endswith(b"\n"):
        complete, tail = payload, b""
    else:
        boundary = payload.rfind(b"\n")
        if boundary < 0:
            complete, tail = b"", payload
        else:
            complete, tail = payload[: boundary + 1], payload[boundary + 1 :]

    for line_number, raw_line in enumerate(complete.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid legacy JSONL line {line_number}: {exc}"
            ) from exc
    return complete, tail


def _complete_csv_prefix(payload: bytes) -> tuple[bytes, bytes]:
    if not payload or payload.endswith(b"\n"):
        complete, tail = payload, b""
    else:
        boundary = payload.rfind(b"\n")
        if boundary < 0:
            complete, tail = b"", payload
        else:
            complete, tail = payload[: boundary + 1], payload[boundary + 1 :]
    if complete:
        try:
            rows = list(csv.reader(
                io.StringIO(complete.decode("utf-8-sig")),
                strict=True,
            ))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ValueError(f"invalid legacy CSV: {exc}") from exc
        if rows:
            width = len(rows[0])
            if width == 0 or any(len(row) != width for row in rows[1:]):
                raise ValueError("invalid legacy CSV row width")
    return complete, tail


def _complete_stream_prefix(name: str, payload: bytes) -> tuple[bytes, bytes]:
    if name.endswith(".jsonl"):
        return _complete_jsonl_prefix(payload)
    if name.endswith(".csv"):
        return _complete_csv_prefix(payload)
    return payload, b""


def _archive_partial_tail(
    runtime_dir: Path,
    name: str,
    tail: bytes,
) -> str:
    recovery = runtime_dir / "recovery"
    target = recovery / f"{name}.partial-tail"
    if target.exists() and target.read_bytes() != tail:
        target = recovery / f"{name}.{_sha256(tail)[:12]}.partial-tail"
    if not target.exists():
        _atomic_write(target, tail)
    return target.relative_to(runtime_dir).as_posix()


def initialize_runtime_store(
    repo: Path | None = None,
    *,
    runtime_dir: Path | None = None,
    legacy_dir: Path | None = None,
    initialized_at: str | None = None,
    code_commit: str | None = None,
) -> MigrationResult:
    """Create the runtime store without overwriting existing live evidence."""

    root = _repo_root(repo)
    target_dir = Path(
        runtime_dir
        or _configured_runtime_dir(root)
        or default_runtime_data_dir(root)
    ).resolve()
    source_dir = Path(legacy_dir or legacy_data_dir(root)).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    preserved: list[str] = []
    archived_tails: list[str] = []
    stream_manifest: dict[str, dict] = {}

    for name in AUTHORITATIVE_STREAMS:
        source = source_dir / name
        target = target_dir / name
        if target.exists():
            existing_payload = target.read_bytes()
            payload, tail = _complete_stream_prefix(name, existing_payload)
            if tail:
                archived_tails.append(
                    _archive_partial_tail(target_dir, name, tail)
                )
                _atomic_write(target, payload)
            preserved.append(name)
            stream_manifest[name] = {
                "action": "preserved",
                "bytes": len(payload),
                "sha256": _sha256(payload),
                "source": "runtime-existing",
            }
            continue
        if not source.is_file():
            stream_manifest[name] = {
                "action": "missing",
                "bytes": 0,
                "sha256": None,
                "source": None,
            }
            continue

        source_payload = source.read_bytes()
        payload, tail = _complete_stream_prefix(name, source_payload)
        if tail:
            archived_tails.append(
                _archive_partial_tail(target_dir, name, tail)
            )
        _atomic_write(target, payload)
        copied.append(name)
        try:
            relative_source = source.relative_to(root).as_posix()
        except ValueError:
            relative_source = str(source)
        stream_manifest[name] = {
            "action": "copied",
            "bytes": len(payload),
            "sha256": _sha256(payload),
            "source": relative_source,
        }

    manifest_path = target_dir / RUNTIME_MANIFEST_NAME
    previous: dict = {}
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    timestamp = (
        initialized_at
        or previous.get("initialized_at")
        or datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    manifest = {
        "schema_version": 1,
        "initialized_at": timestamp,
        "code_commit": code_commit or previous.get("code_commit"),
        "legacy_data_dir": str(source_dir),
        "runtime_data_dir": str(target_dir),
        "streams": stream_manifest,
    }
    _atomic_write(
        manifest_path,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return MigrationResult(
        ok=True,
        runtime_dir=target_dir,
        copied=tuple(copied),
        preserved=tuple(preserved),
        archived_tails=tuple(archived_tails),
        manifest_path=manifest_path,
    )
