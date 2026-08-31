"""Crash-safe telemetry checkpoint and isolated publication.

This module never commits in the production code checkout. It converts
append-only runtime streams into immutable, verified chunks and optionally
publishes those chunks from a separate Git repository on the ``telemetry``
branch.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import time
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import runtime_paths


TELEMETRY_DIR_NAME = ".telemetry"
DEFAULT_STREAM_NAMES = (
    *runtime_paths.AUTHORITATIVE_STREAMS,
    "bot_runtime.log",
    "telegram_media.jsonl",
)
DEFAULT_MAX_CHUNK_BYTES = 4 * 1024 * 1024
DEFAULT_GIT_TIMEOUT_SEC = 30.0
DEFAULT_PUBLISH_LOCK_STALE_SEC = 15 * 60.0
PUBLICATION_STATUS_FILE_NAME = "publication_status.json"
# The raw byte range is the chunk identity. Compressed size/hash are transport
# details: Python 3.11 and 3.14 can emit different gzip headers for the same
# payload, so those fields must not turn equivalent evidence into a conflict.
IMMUTABLE_MANIFEST_FIELDS = (
    "schema_version",
    "stream",
    "byte_start",
    "byte_end",
    "uncompressed_bytes",
    "sha256",
    "compression",
    "payload_file",
)


@dataclass(frozen=True)
class ChunkRecord:
    stream: str
    start: int
    end: int
    payload_path: Path
    manifest_path: Path
    sha256: str


@dataclass(frozen=True)
class CheckpointResult:
    ok: bool
    chunks: tuple[ChunkRecord, ...]
    pending_tail_bytes: dict[str, int]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class MaterializeResult:
    ok: bool
    streams: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class PublishResult:
    ok: bool
    published_files: int
    commit: str | None = None
    error: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_process_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _process_group_kwargs() -> dict:
    if os.name == "nt":
        return {
            "creationflags": getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        }
    return {"start_new_session": True}


def terminate_process_tree(process, timeout_sec: float = 5.0) -> None:
    """Stop a spawned command and every descendant it may have left alive."""
    try:
        if process.poll() is not None:
            return
    except (AttributeError, OSError):
        pass

    pid = getattr(process, "pid", None)
    tree_signal_sent = False
    if pid and os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=max(1.0, float(timeout_sec)),
            )
            tree_signal_sent = True
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    elif pid:
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
            tree_signal_sent = True
        except (AttributeError, OSError, ProcessLookupError, ValueError):
            pass

    if not tree_signal_sent:
        try:
            process.terminate()
        except (AttributeError, OSError):
            pass

    try:
        process.wait(timeout=max(0.1, float(timeout_sec)))
        return
    except (AttributeError, OSError, subprocess.TimeoutExpired):
        pass

    if pid and os.name != "nt":
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGKILL)
        except (AttributeError, OSError, ProcessLookupError, ValueError):
            pass
    else:
        try:
            process.kill()
        except (AttributeError, OSError):
            pass
    try:
        process.wait(timeout=max(0.1, float(timeout_sec)))
    except (AttributeError, OSError, subprocess.TimeoutExpired):
        pass


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _gzip_payloads_match(first: bytes, second: bytes) -> bool:
    if first == second:
        return True
    try:
        return gzip.decompress(first) == gzip.decompress(second)
    except (EOFError, OSError, zlib.error):
        return False


def _canonical_gzip(payload: bytes) -> bytes:
    compressed = bytearray(gzip.compress(payload, compresslevel=6, mtime=0))
    if len(compressed) < 10 or compressed[:3] != b"\x1f\x8b\x08":
        raise ValueError("gzip encoder returned an invalid header")
    # RFC 1952 OS=255 means unknown. Normalizing this byte removes the
    # platform-dependent header emitted by some Python/zlib combinations.
    compressed[9] = 255
    return bytes(compressed)


def _manifest_identity_matches(first: dict, second: dict) -> bool:
    return all(
        first.get(field) == second.get(field)
        for field in IMMUTABLE_MANIFEST_FIELDS
    )


def _validate_stream_name(stream: str) -> str:
    value = str(stream)
    if (
        not value
        or value in {".", ".."}
        or Path(value).name != value
    ):
        raise ValueError(f"invalid stream name: {value}")
    return value


def _sha256_file_prefix(path: Path, size: int) -> str:
    digest = hashlib.sha256()
    remaining = max(0, int(size))
    with path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError(f"{path.name}: exported prefix is incomplete")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


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


def _read_json(path: Path, default: dict | None = None) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(default or {})
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _acquire_named_lock(
    runtime_dir: Path,
    name: str,
    *,
    stale_after_sec: float = DEFAULT_PUBLISH_LOCK_STALE_SEC,
) -> Path | None:
    lock = runtime_dir / TELEMETRY_DIR_NAME / name
    lock.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            descriptor = os.open(
                str(lock),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > stale_after_sec
            except FileNotFoundError:
                continue
            if stale and attempt == 0:
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
                continue
            return None
        payload = (
            json.dumps({"pid": os.getpid(), "created_at": _now_iso()}) + "\n"
        ).encode("utf-8")
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
        return lock
    return None


def _stream_state_path(runtime_dir: Path, stream: str) -> Path:
    return (
        runtime_dir
        / TELEMETRY_DIR_NAME
        / "cursors"
        / f"{stream}.json"
    )


def _outbox_stream_dir(runtime_dir: Path, stream: str) -> Path:
    return runtime_dir / TELEMETRY_DIR_NAME / "outbox" / stream


def _validate_exported_prefix(
    source: Path,
    state: dict,
) -> tuple[int, str | None]:
    offset = int(state.get("offset") or 0)
    size = source.stat().st_size
    if size < offset:
        return offset, f"{source.name}: exported prefix changed (file shrank)"
    if offset <= 0:
        return 0, None
    expected_prefix = str(state.get("prefix_sha256") or "")
    if expected_prefix:
        try:
            actual_prefix = _sha256_file_prefix(source, offset)
        except (OSError, ValueError) as exc:
            return offset, str(exc)
        if actual_prefix != expected_prefix:
            return offset, f"{source.name}: exported prefix changed"
        return offset, None
    anchor_start = int(state.get("anchor_start") or 0)
    expected = str(state.get("anchor_sha256") or "")
    if anchor_start < 0 or anchor_start > offset or not expected:
        return offset, f"{source.name}: invalid telemetry cursor"
    with source.open("rb") as handle:
        handle.seek(anchor_start)
        anchor = handle.read(offset - anchor_start)
    if _sha256(anchor) != expected:
        return offset, f"{source.name}: exported prefix changed"
    return offset, None


def _complete_prefix(payload: bytes) -> tuple[bytes, bytes]:
    if not payload or payload.endswith(b"\n"):
        return payload, b""
    boundary = payload.rfind(b"\n")
    if boundary < 0:
        return b"", payload
    return payload[: boundary + 1], payload[boundary + 1 :]


def _validate_jsonl(stream: str, payload: bytes) -> None:
    if not stream.lower().endswith(".jsonl"):
        return
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"{stream}: invalid JSONL record {line_number}: {exc}"
            ) from exc


def _split_records(payload: bytes, maximum: int) -> Iterable[bytes]:
    if maximum <= 0:
        raise ValueError("max_chunk_bytes must be positive")
    position = 0
    while position < len(payload):
        remaining = payload[position:]
        if len(remaining) <= maximum:
            yield remaining
            return
        boundary = remaining.rfind(b"\n", 0, maximum + 1)
        if boundary >= 0:
            cut = boundary + 1
        else:
            following = remaining.find(b"\n", maximum)
            if following < 0:
                cut = len(remaining)
            else:
                cut = following + 1
        yield remaining[:cut]
        position += cut


def _payload_suffix(stream: str) -> str:
    suffix = Path(stream).suffix.lower().lstrip(".") or "bin"
    return f".{suffix}.gz"


def _write_chunk(
    runtime_dir: Path,
    stream: str,
    start: int,
    payload: bytes,
    *,
    code_commit: str | None,
    created_at: str,
) -> ChunkRecord:
    end = start + len(payload)
    raw_hash = _sha256(payload)
    stem = f"{start:016d}-{end:016d}-{raw_hash[:16]}"
    stream_dir = _outbox_stream_dir(runtime_dir, stream)
    payload_path = stream_dir / f"{stem}{_payload_suffix(stream)}"
    manifest_path = stream_dir / f"{stem}.manifest.json"
    compressed = _canonical_gzip(payload)
    compressed_hash = _sha256(compressed)
    manifest = {
        "schema_version": 1,
        "stream": stream,
        "byte_start": start,
        "byte_end": end,
        "uncompressed_bytes": len(payload),
        "sha256": raw_hash,
        "compression": "gzip",
        "compressed_bytes": len(compressed),
        "compressed_sha256": compressed_hash,
        "payload_file": payload_path.name,
        "created_at": created_at,
        "code_commit": code_commit,
    }
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    if payload_path.exists():
        if not _gzip_payloads_match(payload_path.read_bytes(), compressed):
            raise ValueError(f"conflicting immutable chunk: {payload_path}")
    else:
        _atomic_write(payload_path, compressed)
    if manifest_path.exists():
        existing = _read_json(manifest_path)
        if not _manifest_identity_matches(existing, manifest):
            raise ValueError(f"conflicting chunk manifest: {manifest_path}")
    else:
        _atomic_write(manifest_path, manifest_bytes)
    return ChunkRecord(
        stream=stream,
        start=start,
        end=end,
        payload_path=payload_path,
        manifest_path=manifest_path,
        sha256=raw_hash,
    )


def _cursor_payload(source: Path, offset: int) -> bytes:
    anchor_start = max(0, offset - 4096)
    with source.open("rb") as handle:
        handle.seek(anchor_start)
        anchor = handle.read(offset - anchor_start)
    state = {
        "schema_version": 1,
        "stream": source.name,
        "offset": offset,
        "anchor_start": anchor_start,
        "anchor_sha256": _sha256(anchor),
        "prefix_sha256": _sha256_file_prefix(source, offset),
        "updated_at": _now_iso(),
    }
    return (json.dumps(state, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def checkpoint_runtime(
    runtime_dir: Path,
    *,
    stream_names: Iterable[str] = DEFAULT_STREAM_NAMES,
    max_chunk_bytes: int = DEFAULT_MAX_CHUNK_BYTES,
    code_commit: str | None = None,
    created_at: str | None = None,
) -> CheckpointResult:
    """Checkpoint once; an overlapping exporter owns the same evidence."""

    runtime_dir = Path(runtime_dir).resolve()
    lock = _acquire_named_lock(runtime_dir, "checkpoint.lock")
    if lock is None:
        return CheckpointResult(True, (), {}, ())
    try:
        return _checkpoint_runtime_locked(
            runtime_dir,
            stream_names=stream_names,
            max_chunk_bytes=max_chunk_bytes,
            code_commit=code_commit,
            created_at=created_at,
        )
    finally:
        lock.unlink(missing_ok=True)


def _checkpoint_runtime_locked(
    runtime_dir: Path,
    *,
    stream_names: Iterable[str] = DEFAULT_STREAM_NAMES,
    max_chunk_bytes: int = DEFAULT_MAX_CHUNK_BYTES,
    code_commit: str | None = None,
    created_at: str | None = None,
) -> CheckpointResult:
    """Checkpoint complete append-only records without any network operation."""

    runtime_dir = Path(runtime_dir).resolve()
    timestamp = created_at or _now_iso()
    chunks: list[ChunkRecord] = []
    pending: dict[str, int] = {}
    errors: list[str] = []

    for stream in tuple(stream_names):
        try:
            stream = _validate_stream_name(stream)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        source = runtime_dir / stream
        if not source.is_file():
            continue
        state_path = _stream_state_path(runtime_dir, stream)
        try:
            state = _read_json(state_path, {})
            offset, prefix_error = _validate_exported_prefix(source, state)
            if prefix_error:
                errors.append(prefix_error)
                continue
            with source.open("rb") as handle:
                handle.seek(offset)
                appended = handle.read()
            complete, tail = _complete_prefix(appended)
            pending[stream] = len(tail)
            _validate_jsonl(stream, complete)
            current = offset
            for piece in _split_records(complete, int(max_chunk_bytes)):
                record = _write_chunk(
                    runtime_dir,
                    stream,
                    current,
                    piece,
                    code_commit=code_commit,
                    created_at=timestamp,
                )
                chunks.append(record)
                current = record.end
                _atomic_write(state_path, _cursor_payload(source, current))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{stream}: {exc}")

    return CheckpointResult(
        ok=not errors,
        chunks=tuple(chunks),
        pending_tail_bytes=pending,
        errors=tuple(errors),
    )


def _manifest_payload_path(manifest_path: Path, manifest: dict) -> Path:
    payload_name = str(manifest.get("payload_file") or "")
    if not payload_name or Path(payload_name).name != payload_name:
        raise ValueError(f"invalid payload filename in {manifest_path}")
    return manifest_path.parent / payload_name


def _select_contiguous_payload(
    stream: str,
    chunks: list[tuple[int, int, str, bytes]],
) -> bytes:
    """Choose the unique chain from byte zero with the greatest coverage."""

    if not chunks:
        return b""
    by_start: dict[int, list[tuple[int, bytes]]] = {}
    maximum_end = 0
    for start, end, _raw_hash, payload in chunks:
        if start < 0 or end <= start or len(payload) != end - start:
            raise ValueError(
                f"invalid contiguous chunk for {stream}: {start}:{end}"
            )
        by_start.setdefault(start, []).append((end, payload))
        maximum_end = max(maximum_end, end)

    if 0 not in by_start:
        first = min(by_start)
        raise ValueError(f"range gap: expected 0, got {first}")

    # Determine graph reachability using byte offsets only. The previous
    # implementation stored a fully concatenated suffix at every offset,
    # which made a stream with N small chunks consume O(N^2) bytes.
    reachable_from_zero = {0}
    for start in sorted(by_start):
        if start not in reachable_from_zero:
            continue
        reachable_from_zero.update(end for end, _payload in by_start[start])

    final_end = max(reachable_from_zero)
    if maximum_end not in reachable_from_zero:
        later = sorted(start for start in by_start if start >= final_end)
        detail = str(later[0]) if later else "overlapping unreachable range"
        raise ValueError(f"range gap: expected {final_end}, got {detail}")

    reaches_maximum = {maximum_end}
    for start in sorted(by_start, reverse=True):
        if any(end in reaches_maximum for end, _payload in by_start[start]):
            reaches_maximum.add(start)

    # Build one complete path exactly once. Every other complete-path chunk
    # is then compared against the same absolute byte range. This accepts
    # equivalent histories split at different boundaries and fails closed on
    # any real content ambiguity without retaining quadratic suffix copies.
    cursor = 0
    parts: list[bytes] = []
    while cursor < maximum_end:
        options = [
            (end, payload)
            for end, payload in by_start.get(cursor, [])
            if end in reaches_maximum
        ]
        if not options:
            raise ValueError(
                f"range gap: expected {cursor}, got unreachable maximum"
            )
        end, payload = max(options, key=lambda option: option[0])
        parts.append(payload)
        cursor = end

    selected = b"".join(parts)
    selected_view = memoryview(selected)
    for start, options in by_start.items():
        if start not in reachable_from_zero:
            continue
        for end, payload in options:
            if end not in reaches_maximum:
                continue
            if selected_view[start:end] != payload:
                raise ValueError(
                    f"ambiguous contiguous history for {stream} "
                    f"at byte {maximum_end}"
                )
    return selected


def _assemble_chunks(
    outbox_dir: Path,
) -> tuple[dict[str, bytes], tuple[str, ...]]:
    """Validate every chunk before allowing any output file to change."""

    outbox_dir = Path(outbox_dir).resolve()
    grouped: dict[str, list[tuple[dict, Path]]] = {}
    errors: list[str] = []
    for path in sorted(outbox_dir.rglob("*.manifest.json")):
        try:
            manifest = _read_json(path)
            stream = _validate_stream_name(str(manifest["stream"]))
            grouped.setdefault(stream, []).append((manifest, path))
        except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{path}: {exc}")

    payloads: dict[str, bytes] = {}
    for stream, entries in sorted(grouped.items()):
        verified: list[tuple[int, int, str, bytes]] = []
        seen_ranges: dict[tuple[int, int], str] = {}
        stream_failed = False
        for manifest, manifest_path in entries:
            try:
                start = int(manifest["byte_start"])
                end = int(manifest["byte_end"])
                raw_hash = str(manifest["sha256"])
                if start < 0 or end <= start:
                    raise ValueError(f"invalid range {start}:{end}")
                range_key = (start, end)
                if range_key in seen_ranges:
                    if seen_ranges[range_key] != raw_hash:
                        raise ValueError(
                            f"conflicting duplicate range {start}:{end}"
                        )
                    continue
                if str(manifest.get("compression") or "") != "gzip":
                    raise ValueError("unsupported compression")
                payload_path = _manifest_payload_path(manifest_path, manifest)
                compressed = payload_path.read_bytes()
                if _sha256(compressed) != str(
                    manifest.get("compressed_sha256") or ""
                ):
                    raise ValueError("compressed hash mismatch")
                if len(compressed) != int(manifest["compressed_bytes"]):
                    raise ValueError("compressed size mismatch")
                payload = gzip.decompress(compressed)
                if len(payload) != end - start:
                    raise ValueError("uncompressed size mismatch")
                if len(payload) != int(manifest["uncompressed_bytes"]):
                    raise ValueError("manifest size mismatch")
                if _sha256(payload) != raw_hash:
                    raise ValueError("uncompressed hash mismatch")
                _validate_jsonl(stream, payload)
                seen_ranges[range_key] = raw_hash
                verified.append((start, end, raw_hash, payload))
            except (
                KeyError,
                OSError,
                ValueError,
                gzip.BadGzipFile,
                EOFError,
            ) as exc:
                errors.append(f"{stream}: {exc}")
                stream_failed = True
                break
        if stream_failed:
            continue
        try:
            payloads[stream] = _select_contiguous_payload(stream, verified)
        except ValueError as exc:
            errors.append(f"{stream}: {exc}")

    if errors:
        return {}, tuple(errors)
    return payloads, ()


def _recover_materialization(output_dir: Path) -> None:
    marker = output_dir / runtime_paths.MATERIALIZE_MARKER_NAME
    if not marker.is_file():
        return
    state = _read_json(marker)
    transaction = Path(str(state.get("transaction_dir") or "")).resolve()
    expected_parent = output_dir.parent.resolve()
    if (
        transaction.parent != expected_parent
        or not transaction.name.startswith(f".{output_dir.name}.materialize-")
    ):
        raise ValueError("invalid materialization recovery directory")
    records = state.get("files")
    if not isinstance(records, list):
        raise ValueError("invalid materialization recovery manifest")
    for record in reversed(records):
        if not isinstance(record, dict):
            raise ValueError("invalid materialization recovery record")
        name = str(record.get("name") or "")
        if not name or Path(name).name != name:
            raise ValueError("invalid materialization recovery filename")
        target = output_dir / name
        backup = transaction / "backup" / name
        if bool(record.get("had_original")):
            if not backup.is_file():
                raise ValueError(f"missing materialization backup: {name}")
            os.replace(backup, target)
        else:
            target.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    shutil.rmtree(transaction, ignore_errors=True)


def _commit_materialized_files(
    output_dir: Path,
    payloads: dict[str, bytes],
) -> None:
    """Install one verified corpus with rollback after interruption."""

    output_dir = Path(output_dir).resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    _recover_materialization(output_dir)
    transaction = output_dir.parent / (
        f".{output_dir.name}.materialize-{uuid.uuid4().hex}"
    )
    staged = transaction / "new"
    backup = transaction / "backup"
    records: list[dict] = []
    marker = output_dir / runtime_paths.MATERIALIZE_MARKER_NAME
    names = sorted(
        payloads,
        key=lambda name: (name == runtime_paths.RUNTIME_MANIFEST_NAME, name),
    )
    try:
        for name in names:
            if not name or Path(name).name != name:
                raise ValueError(f"invalid materialized filename: {name}")
            _atomic_write(staged / name, payloads[name])
            target = output_dir / name
            had_original = target.is_file()
            records.append({"name": name, "had_original": had_original})
            if had_original:
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup / name)

        marker_payload = {
            "schema_version": 1,
            "transaction_dir": str(transaction.resolve()),
            "files": records,
        }
        _atomic_write(
            marker,
            (json.dumps(marker_payload, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        for name in names:
            os.replace(staged / name, output_dir / name)
        marker.unlink()
        shutil.rmtree(transaction, ignore_errors=True)
    except BaseException:
        if marker.exists():
            try:
                _recover_materialization(output_dir)
            except BaseException:
                pass
        else:
            shutil.rmtree(transaction, ignore_errors=True)
        raise


def materialize_chunks(outbox_dir: Path, output_dir: Path) -> MaterializeResult:
    """Verify all streams, then install them as one recoverable transaction."""

    payloads, errors = _assemble_chunks(outbox_dir)
    if errors:
        return MaterializeResult(False, (), errors)
    try:
        _commit_materialized_files(output_dir, payloads)
    except (OSError, ValueError) as exc:
        return MaterializeResult(False, (), (str(exc),))

    return MaterializeResult(
        ok=True,
        streams=tuple(sorted(payloads)),
        errors=(),
    )


def _run_git(
    cwd: Path,
    *args: str,
    timeout_sec: float = DEFAULT_GIT_TIMEOUT_SEC,
) -> subprocess.CompletedProcess:
    command = ["git", *args]
    process = subprocess.Popen(
        command,
        cwd=Path(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_process_group_kwargs(),
    )
    try:
        stdout, stderr = process.communicate(timeout=float(timeout_sec))
        return subprocess.CompletedProcess(
            command,
            int(process.returncode or 0),
            stdout=_as_process_text(stdout),
            stderr=_as_process_text(stderr),
        )
    except subprocess.TimeoutExpired as exc:
        terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            stdout, stderr = exc.stdout, exc.stderr
        stderr_text = _as_process_text(stderr or exc.stderr)
        timeout_message = f"git {' '.join(args)} timed out"
        return subprocess.CompletedProcess(
            command,
            124,
            stdout=_as_process_text(stdout or exc.stdout),
            stderr=(
                f"{stderr_text.rstrip()}\n{timeout_message}".strip()
                if stderr_text
                else timeout_message
            ),
        )


def _publication_outbox_files(runtime_dir: Path) -> list[Path]:
    outbox = Path(runtime_dir) / TELEMETRY_DIR_NAME / "outbox"
    if not outbox.is_dir():
        return []
    return sorted(
        path
        for path in outbox.rglob("*")
        if path.is_file()
        and (
            path.name.endswith(".gz")
            or path.name.endswith(".manifest.json")
        )
    )


def publication_health(
    runtime_dir: Path,
    *,
    now: float | None = None,
) -> dict:
    """Describe locally safe telemetry that has not reached the remote yet."""
    runtime_dir = Path(runtime_dir).resolve()
    files = _publication_outbox_files(runtime_dir)
    current_time = time.time() if now is None else float(now)
    oldest_mtime = min(
        (path.stat().st_mtime for path in files),
        default=None,
    )
    status_path = (
        runtime_dir / TELEMETRY_DIR_NAME / PUBLICATION_STATUS_FILE_NAME
    )
    status = {}
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            status = {}
    return {
        "pending_files": len(files),
        "pending_chunks": sum(
            path.name.endswith(".manifest.json") for path in files
        ),
        "oldest_pending_age_s": (
            max(0.0, current_time - oldest_mtime)
            if oldest_mtime is not None
            else 0.0
        ),
        "latest_error": status.get("error"),
        "last_attempt_at": status.get("attempted_at"),
        "last_success_at": status.get("last_success_at"),
    }


def _record_publication_status(
    runtime_dir: Path,
    result: PublishResult,
) -> None:
    runtime_dir = Path(runtime_dir).resolve()
    status_path = (
        runtime_dir / TELEMETRY_DIR_NAME / PUBLICATION_STATUS_FILE_NAME
    )
    previous = {}
    if status_path.is_file():
        try:
            previous = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            previous = {}
    attempted_at = _now_iso()
    health = publication_health(runtime_dir)
    payload = {
        "schema_version": 1,
        "attempted_at": attempted_at,
        "ok": bool(result.ok),
        "published_files": int(result.published_files),
        "commit": result.commit,
        "error": result.error,
        "pending_files": health["pending_files"],
        "pending_chunks": health["pending_chunks"],
        "last_success_at": (
            attempted_at if result.ok else previous.get("last_success_at")
        ),
    }
    _atomic_write(
        status_path,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )


def _git_output(
    cwd: Path,
    *args: str,
    timeout_sec: float = DEFAULT_GIT_TIMEOUT_SEC,
) -> str | None:
    result = _run_git(cwd, *args, timeout_sec=timeout_sec)
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip()


def _checkout_isolation_error(
    source_repo: Path,
    checkout: Path,
    *,
    timeout_sec: float,
) -> str | None:
    source_repo = Path(source_repo).resolve()
    checkout = Path(checkout).resolve()
    if checkout == source_repo or checkout in source_repo.parents:
        return "telemetry checkout is not isolated from the source repository"
    if source_repo in checkout.parents:
        relative = checkout.relative_to(source_repo).as_posix()
        ignored = _run_git(
            source_repo,
            "check-ignore",
            "--quiet",
            "--",
            relative,
            timeout_sec=timeout_sec,
        )
        if ignored.returncode != 0:
            return (
                "telemetry checkout inside the source repository must be "
                "Git-ignored"
            )
    if (checkout / ".git").exists():
        top_level = _git_output(
            checkout,
            "rev-parse",
            "--show-toplevel",
            timeout_sec=timeout_sec,
        )
        if top_level and Path(top_level).resolve() == source_repo:
            return "telemetry checkout resolves to the source repository"
    return None


def _ensure_checkout(
    checkout: Path,
    remote_url: str,
    branch: str,
    timeout_sec: float,
) -> tuple[bool, str | None]:
    checkout.mkdir(parents=True, exist_ok=True)
    if not (checkout / ".git").exists():
        initialized = _run_git(
            checkout, "init", timeout_sec=timeout_sec
        )
        if initialized.returncode != 0:
            return False, initialized.stderr or initialized.stdout
        _run_git(
            checkout,
            "config",
            "user.name",
            "Signal Copier Telemetry",
            timeout_sec=timeout_sec,
        )
        _run_git(
            checkout,
            "config",
            "user.email",
            "telemetry@local.invalid",
            timeout_sec=timeout_sec,
        )
        added = _run_git(
            checkout,
            "remote",
            "add",
            "origin",
            remote_url,
            timeout_sec=timeout_sec,
        )
        if added.returncode != 0:
            return False, added.stderr or added.stdout
    else:
        current_url = _git_output(
            checkout,
            "remote",
            "get-url",
            "origin",
            timeout_sec=timeout_sec,
        )
        if current_url != remote_url:
            changed = _run_git(
                checkout,
                "remote",
                "set-url",
                "origin",
                remote_url,
                timeout_sec=timeout_sec,
            )
            if changed.returncode != 0:
                return False, changed.stderr or changed.stdout

    remote_probe = _run_git(
        checkout,
        "ls-remote",
        "--exit-code",
        "--heads",
        "origin",
        f"refs/heads/{branch}",
        timeout_sec=timeout_sec,
    )
    remote_exists = remote_probe.returncode == 0
    if remote_probe.returncode not in {0, 2}:
        return False, remote_probe.stderr or remote_probe.stdout

    local_head = _git_output(
        checkout, "rev-parse", "HEAD", timeout_sec=timeout_sec
    )
    current_branch = _git_output(
        checkout,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        timeout_sec=timeout_sec,
    )

    if remote_exists:
        fetched = _run_git(
            checkout,
            "fetch",
            "origin",
            branch,
            timeout_sec=timeout_sec,
        )
        if fetched.returncode != 0:
            return False, fetched.stderr or fetched.stdout
        if local_head is None:
            attached = _run_git(
                checkout,
                "switch",
                "-C",
                branch,
                "FETCH_HEAD",
                timeout_sec=timeout_sec,
            )
            if attached.returncode != 0:
                return False, attached.stderr or attached.stdout
        else:
            if current_branch != branch:
                switched = _run_git(
                    checkout,
                    "switch",
                    "-C",
                    branch,
                    local_head,
                    timeout_sec=timeout_sec,
                )
                if switched.returncode != 0:
                    return False, switched.stderr or switched.stdout
            ancestor = _run_git(
                checkout,
                "merge-base",
                "--is-ancestor",
                "FETCH_HEAD",
                "HEAD",
                timeout_sec=timeout_sec,
            )
            if ancestor.returncode != 0:
                rebased = _run_git(
                    checkout,
                    "rebase",
                    "FETCH_HEAD",
                    timeout_sec=timeout_sec,
                )
                if rebased.returncode != 0:
                    _run_git(
                        checkout,
                        "rebase",
                        "--abort",
                        timeout_sec=timeout_sec,
                    )
                    return False, rebased.stderr or rebased.stdout
    elif local_head is None:
        orphan = _run_git(
            checkout,
            "switch",
            "--orphan",
            branch,
            timeout_sec=timeout_sec,
        )
        if orphan.returncode != 0:
            return False, orphan.stderr or orphan.stdout
    elif current_branch != branch:
        switched = _run_git(
            checkout,
            "switch",
            "-C",
            branch,
            local_head,
            timeout_sec=timeout_sec,
        )
        if switched.returncode != 0:
            return False, switched.stderr or switched.stdout
    return True, None


def _acquire_publish_lock(
    runtime_dir: Path,
    *,
    stale_after_sec: float = DEFAULT_PUBLISH_LOCK_STALE_SEC,
) -> Path | None:
    return _acquire_named_lock(
        runtime_dir,
        "publish.lock",
        stale_after_sec=stale_after_sec,
    )


def _remove_confirmed_outbox_files(files: Iterable[Path], outbox: Path) -> None:
    for path in files:
        path.unlink(missing_ok=True)
    directories = sorted(
        (path for path in outbox.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass


def _published_file_matches(destination: Path, payload: bytes) -> bool:
    existing = destination.read_bytes()
    if existing == payload:
        return True
    if destination.name.endswith(".gz"):
        return _gzip_payloads_match(existing, payload)
    if not destination.name.endswith(".manifest.json"):
        return False
    try:
        previous = json.loads(existing.decode("utf-8"))
        candidate = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(previous, dict) or not isinstance(candidate, dict):
        return False
    return _manifest_identity_matches(previous, candidate)


def _materialized_runtime_manifest_payload(
    output_dir: Path,
    payloads: dict[str, bytes],
    *,
    branch: str,
    telemetry_commit: str | None,
) -> bytes:
    stream_records = {}
    for stream, payload in sorted(payloads.items()):
        stream_records[stream] = {
            "action": "materialized",
            "bytes": len(payload),
            "sha256": _sha256(payload),
            "source": f"{branch}:{stream}",
        }
    manifest = {
        "schema_version": 1,
        "initialized_at": _now_iso(),
        "source": "telemetry_branch",
        "telemetry_branch": branch,
        "telemetry_commit": telemetry_commit,
        "runtime_data_dir": str(output_dir),
        "streams": stream_records,
    }
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def publish_outbox(
    source_repo: Path,
    runtime_dir: Path,
    *,
    remote_url: str | None = None,
    checkout_dir: Path | None = None,
    branch: str = "telemetry",
    timeout_sec: float = DEFAULT_GIT_TIMEOUT_SEC,
) -> PublishResult:
    """Publish once; another publisher never shares the isolated checkout."""

    runtime_dir = Path(runtime_dir).resolve()
    lock = _acquire_publish_lock(runtime_dir)
    if lock is None:
        result = PublishResult(
            False,
            0,
            error="telemetry publication is already running",
        )
        _record_publication_status(runtime_dir, result)
        return result
    try:
        result = _publish_outbox_locked(
            source_repo,
            runtime_dir,
            remote_url=remote_url,
            checkout_dir=checkout_dir,
            branch=branch,
            timeout_sec=timeout_sec,
        )
        _record_publication_status(runtime_dir, result)
        return result
    finally:
        lock.unlink(missing_ok=True)


def _publish_outbox_locked(
    source_repo: Path,
    runtime_dir: Path,
    *,
    remote_url: str | None = None,
    checkout_dir: Path | None = None,
    branch: str = "telemetry",
    timeout_sec: float = DEFAULT_GIT_TIMEOUT_SEC,
) -> PublishResult:
    """Publish immutable chunks without mutating the source repository."""

    source_repo = Path(source_repo).resolve()
    runtime_dir = Path(runtime_dir).resolve()
    outbox = runtime_dir / TELEMETRY_DIR_NAME / "outbox"
    files = sorted(
        path
        for path in outbox.rglob("*")
        if path.is_file()
        and (path.name.endswith(".gz") or path.name.endswith(".manifest.json"))
    )
    if not files:
        return PublishResult(ok=True, published_files=0)
    if remote_url is None:
        remote_url = _git_output(
            source_repo,
            "remote",
            "get-url",
            "--push",
            "origin",
            timeout_sec=timeout_sec,
        )
    if not remote_url:
        return PublishResult(False, 0, error="origin remote is unavailable")
    checkout = Path(
        checkout_dir
        or runtime_dir / TELEMETRY_DIR_NAME / "publisher-repo"
    ).resolve()
    isolation_error = _checkout_isolation_error(
        source_repo,
        checkout,
        timeout_sec=timeout_sec,
    )
    if isolation_error:
        return PublishResult(False, 0, error=isolation_error)

    try:
        ready, error = _ensure_checkout(
            checkout, remote_url, branch, timeout_sec
        )
        if not ready:
            return PublishResult(False, 0, error=str(error or "checkout failed"))
        copied = 0
        destination_root = checkout / "chunks"
        for source in files:
            relative = source.relative_to(outbox)
            destination = destination_root / relative
            payload = source.read_bytes()
            if destination.exists():
                if not _published_file_matches(destination, payload):
                    return PublishResult(
                        False,
                        copied,
                        error=f"remote chunk conflict: {relative.as_posix()}",
                    )
                continue
            _atomic_write(destination, payload)
            copied += 1

        added = _run_git(
            checkout, "add", "chunks", timeout_sec=timeout_sec
        )
        if added.returncode != 0:
            return PublishResult(False, copied, error=added.stderr or added.stdout)
        dirty = _run_git(
            checkout,
            "diff",
            "--cached",
            "--quiet",
            timeout_sec=timeout_sec,
        )
        if dirty.returncode == 1:
            committed = _run_git(
                checkout,
                "commit",
                "-m",
                f"telemetry: publish {copied} immutable files",
                timeout_sec=timeout_sec,
            )
            if committed.returncode != 0:
                return PublishResult(
                    False, copied, error=committed.stderr or committed.stdout
                )
        elif dirty.returncode != 0:
            return PublishResult(False, copied, error=dirty.stderr or dirty.stdout)

        pushed = _run_git(
            checkout,
            "push",
            "origin",
            f"HEAD:{branch}",
            timeout_sec=timeout_sec,
        )
        if pushed.returncode != 0:
            return PublishResult(False, copied, error=pushed.stderr or pushed.stdout)
        commit = _git_output(
            checkout, "rev-parse", "HEAD", timeout_sec=timeout_sec
        )
        _remove_confirmed_outbox_files(files, outbox)
        return PublishResult(True, len(files), commit=commit)
    except (OSError, ValueError) as exc:
        return PublishResult(False, 0, error=str(exc))


def pull_and_materialize(
    source_repo: Path,
    output_dir: Path,
    *,
    checkout_dir: Path | None = None,
    branch: str = "telemetry",
    timeout_sec: float = DEFAULT_GIT_TIMEOUT_SEC,
) -> MaterializeResult:
    source_repo = Path(source_repo).resolve()
    remote_url = _git_output(
        source_repo,
        "remote",
        "get-url",
        "origin",
        timeout_sec=timeout_sec,
    )
    if not remote_url:
        return MaterializeResult(False, (), ("origin remote is unavailable",))
    checkout = Path(
        checkout_dir
        or source_repo / "runtime_telemetry_checkout"
    ).resolve()
    isolation_error = _checkout_isolation_error(
        source_repo,
        checkout,
        timeout_sec=timeout_sec,
    )
    if isolation_error:
        return MaterializeResult(False, (), (isolation_error,))
    ready, error = _ensure_checkout(checkout, remote_url, branch, timeout_sec)
    if not ready:
        return MaterializeResult(False, (), (str(error or "fetch failed"),))
    payloads, errors = _assemble_chunks(checkout / "chunks")
    if errors:
        return MaterializeResult(False, (), errors)
    if not payloads:
        return MaterializeResult(True, (), ())
    try:
        telemetry_commit = _git_output(
            checkout,
            "rev-parse",
            "HEAD",
            timeout_sec=timeout_sec,
        )
        files = dict(payloads)
        files[runtime_paths.RUNTIME_MANIFEST_NAME] = (
            _materialized_runtime_manifest_payload(
                output_dir,
                payloads,
                branch=branch,
                telemetry_commit=telemetry_commit,
            )
        )
        _commit_materialized_files(output_dir, files)
    except (OSError, ValueError) as exc:
        return MaterializeResult(False, (), (str(exc),))
    return MaterializeResult(
        True,
        tuple(sorted(payloads)),
        (),
    )


def _code_commit(repo: Path) -> str | None:
    return _git_output(repo, "rev-parse", "HEAD", timeout_sec=5)


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Checkpoint and transport runtime telemetry independently"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--checkpoint", action="store_true")
    mode.add_argument("--publish-once", action="store_true")
    mode.add_argument("--pull", action="store_true")
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--checkout-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=DEFAULT_GIT_TIMEOUT_SEC)
    args = parser.parse_args(argv)

    if args.pull:
        output = Path(
            args.output_dir
            or args.runtime_dir
            or runtime_paths.default_runtime_data_dir(ROOT)
        ).resolve()
        result = pull_and_materialize(
            ROOT,
            output,
            checkout_dir=args.checkout_dir,
            timeout_sec=args.timeout,
        )
        if result.ok:
            print(
                f"[Telemetry] materializados {len(result.streams)} streams "
                f"en {output}",
                flush=True,
            )
            return 0
        print(f"[Telemetry] ERROR: {'; '.join(result.errors)}", flush=True)
        return 1

    runtime = Path(
        args.runtime_dir or runtime_paths.active_data_dir(ROOT)
    ).resolve()
    checkpoint = checkpoint_runtime(
        runtime,
        code_commit=_code_commit(ROOT),
    )
    if not checkpoint.ok:
        print(f"[Telemetry] ERROR: {'; '.join(checkpoint.errors)}", flush=True)
        return 1
    print(
        f"[Telemetry] checkpoint local: {len(checkpoint.chunks)} fragmentos",
        flush=True,
    )
    if args.checkpoint:
        return 0
    published = publish_outbox(
        ROOT,
        runtime,
        checkout_dir=args.checkout_dir,
        timeout_sec=args.timeout,
    )
    if published.ok:
        print(
            f"[Telemetry] publicados {published.published_files} archivos",
            flush=True,
        )
        return 0
    print(
        f"[Telemetry] publicacion pendiente: {published.error}",
        flush=True,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(cli())
