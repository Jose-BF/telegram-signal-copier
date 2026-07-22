"""Crash-safe recovery of production evidence before Git synchronization."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import runtime_paths

GIT_TIMEOUT_SEC = float(os.getenv("BOT_GIT_TIMEOUT_SEC", "15"))

AUTHORITATIVE_RUNTIME_PATHS = frozenset({
    "data/trade_events.jsonl",
    "data/trade_events_TEST.jsonl",
    "data/trade_journal.csv",
    "data/trade_journal_TEST.csv",
})

REBUILDABLE_OUTPUT_PATHS = frozenset({
    "data/accounting_replay_audit.jsonl",
    "data/accounting_replay_audit_status.json",
    "data/broker_money_contract.json",
    "data/ledger.jsonl",
    "data/log_learning_report.json",
    "data/log_learning_status.json",
    "data/log_pattern_registry.json",
    "data/money_tick_cache_status.json",
    "data/observed_tick_replay_audit.jsonl",
    "data/observed_tick_replay_status.json",
    "data/provider_signal_catalog.json",
    "data/reconcile_status.json",
    "data/replay_readiness_report.json",
    "data/replay_status.json",
    "data/replay_tick_cache_status.json",
    "data/replay_trades.jsonl",
    "data/strategy_farm.json",
})
REBUILDABLE_OUTPUT_PREFIXES = ("data/simulation_runs/",)
CANONICAL_VERSIONED_OUTPUT_PATHS = frozenset({
    "data/provider_signal_catalog.json",
})


@dataclass(frozen=True)
class RecoveryResult:
    ok: bool
    action: str
    source_paths: tuple[str, ...] = ()
    restored_paths: tuple[str, ...] = ()
    archived_paths: tuple[str, ...] = ()
    unsafe_paths: tuple[str, ...] = ()
    commit: str | None = None
    error: str | None = None


def _run_git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    command = ["git", *args]
    try:
        return subprocess.run(
            command,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            124,
            stdout=exc.stdout or "",
            stderr=f"git {' '.join(args)} timed out after {GIT_TIMEOUT_SEC:g}s",
        )


def _paths(repo_dir: Path, *args: str) -> tuple[str, ...] | None:
    result = _run_git(repo_dir, *args)
    if result.returncode != 0:
        return None
    return tuple(sorted({
        path.replace("\\", "/")
        for path in (result.stdout or "").split("\0")
        if path
    }))


def _is_rebuildable(path: str) -> bool:
    return (
        path in REBUILDABLE_OUTPUT_PATHS
        or any(path.startswith(prefix) for prefix in REBUILDABLE_OUTPUT_PREFIXES)
    )


def _failure(
    action: str,
    error: str,
    *,
    source_paths: tuple[str, ...] = (),
    restored_paths: tuple[str, ...] = (),
    archived_paths: tuple[str, ...] = (),
    unsafe_paths: tuple[str, ...] = (),
) -> RecoveryResult:
    return RecoveryResult(
        ok=False,
        action=action,
        source_paths=source_paths,
        restored_paths=restored_paths,
        archived_paths=archived_paths,
        unsafe_paths=unsafe_paths,
        error=error,
    )


def _archive_untracked_output(
    repo_dir: Path,
    relative_path: str,
    archive_root: Path,
) -> None:
    source = (repo_dir / relative_path).resolve()
    source.relative_to(repo_dir.resolve())
    destination = (archive_root / relative_path).resolve()
    destination.relative_to(archive_root.resolve())
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        counter = 1
        while destination.with_name(f"{destination.name}.{counter}").exists():
            counter += 1
        destination = destination.with_name(f"{destination.name}.{counter}")
    shutil.move(str(source), str(destination))


def _archive_copy(
    repo_dir: Path,
    relative_path: str,
    archive_root: Path,
) -> None:
    source = (repo_dir / relative_path).resolve()
    source.relative_to(repo_dir.resolve())
    destination = (archive_root / relative_path).resolve()
    destination.relative_to(archive_root.resolve())
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _archive_tail(
    archive_root: Path,
    relative_path: str,
    payload: bytes,
) -> str:
    archived_path = f"{relative_path}.partial-tail"
    destination = (archive_root / archived_path).resolve()
    destination.relative_to(archive_root.resolve())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return archived_path


def _git_blob_bytes(repo_dir: Path, relative_path: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{relative_path}"],
            cwd=repo_dir,
            capture_output=True,
            text=False,
            check=False,
            timeout=min(GIT_TIMEOUT_SEC, 10.0),
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return b""
    return result.stdout or b""


def _repair_partial_tail(
    path: Path,
    relative_path: str,
    archive_root: Path,
) -> str | None:
    raw = path.read_bytes()
    if not raw or raw.endswith(b"\n"):
        return None
    boundary = raw.rfind(b"\n")
    valid = raw[:boundary + 1] if boundary >= 0 else b""
    tail = raw[boundary + 1:]
    archived_path = _archive_tail(archive_root, relative_path, tail)
    path.write_bytes(valid)
    return archived_path


def _validate_runtime_source(
    repo_dir: Path,
    relative_path: str,
    archive_root: Path,
) -> tuple[bool, str | None, tuple[str, ...]]:
    path = repo_dir / relative_path
    archived: list[str] = []
    try:
        repaired_tail = _repair_partial_tail(
            path,
            relative_path,
            archive_root,
        )
        if repaired_tail:
            archived.append(repaired_tail)
        current = path.read_bytes()
    except OSError as exc:
        return False, str(exc), tuple(archived)

    if relative_path.endswith((".jsonl", ".csv")):
        baseline = _git_blob_bytes(repo_dir, relative_path)
        if baseline is None:
            return False, "could not read the committed raw baseline", tuple(archived)
        normalized_current = current.replace(b"\r\n", b"\n")
        normalized_baseline = baseline.replace(b"\r\n", b"\n")
        if not normalized_current.startswith(normalized_baseline):
            kind = "JSONL" if relative_path.endswith(".jsonl") else "CSV"
            return (
                False,
                f"{kind} runtime evidence is not append-only",
                tuple(archived),
            )
        appended = normalized_current[len(normalized_baseline):]
        if relative_path.endswith(".jsonl"):
            for line_number, raw_line in enumerate(
                appended.splitlines(), start=1
            ):
                if not raw_line.strip():
                    continue
                try:
                    json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    return (
                        False,
                        f"invalid appended JSONL line {line_number}: {exc}",
                        tuple(archived),
                    )
    if relative_path.endswith(".csv"):
        try:
            rows = list(csv.reader(
                io.StringIO(current.decode("utf-8-sig")),
                strict=True,
            ))
        except (UnicodeDecodeError, csv.Error) as exc:
            return False, f"invalid runtime CSV: {exc}", tuple(archived)
        if rows:
            width = len(rows[0])
            if width == 0 or any(len(row) != width for row in rows[1:]):
                return False, "runtime CSV has an incomplete row", tuple(archived)
    return True, None, tuple(archived)


def _append_durable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _merge_source_into_runtime(
    repo_dir: Path,
    relative_path: str,
    runtime_dir: Path,
) -> tuple[bool, str | None]:
    """Preserve a legacy tracked stream without ever rewriting live data."""

    source = repo_dir / relative_path
    target = runtime_dir / Path(relative_path).name
    try:
        source_payload = source.read_bytes()
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source_payload)
            return True, None
        runtime_payload = target.read_bytes()
        if runtime_payload == source_payload:
            return True, None
        if source_payload.startswith(runtime_payload):
            _append_durable(target, source_payload[len(runtime_payload):])
            return True, None
        if runtime_payload.startswith(source_payload):
            return True, None
        return False, (
            f"legacy/runtime evidence diverged for {relative_path}; "
            "both copies were preserved"
        )
    except OSError as exc:
        return False, str(exc)


def prepare_runtime_worktree(
    repo_dir: Path,
    *,
    timestamp: str | None = None,
    runtime_dir: Path | None = None,
) -> RecoveryResult:
    """Move legacy evidence out of Git and repair reproducible output.

    This function intentionally never stages or commits. The production
    checkout is returned to ``HEAD`` only after every complete raw record is
    present in the ignored runtime store.
    """
    repo_dir = Path(repo_dir).resolve()
    runtime_dir = Path(
        runtime_dir or runtime_paths.default_runtime_data_dir(repo_dir)
    ).resolve()
    timestamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    tracked = _paths(repo_dir, "diff", "--name-only", "-z", "HEAD", "--")
    deleted = _paths(
        repo_dir,
        "diff",
        "--diff-filter=D",
        "--name-only",
        "-z",
        "HEAD",
        "--",
    )
    untracked = _paths(
        repo_dir,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    if tracked is None or deleted is None or untracked is None:
        return _failure("inspection_failed", "could not inspect Git worktree")

    changed = set(tracked) | set(untracked)
    source_paths = tuple(sorted(changed & AUTHORITATIVE_RUNTIME_PATHS))
    generated_paths = tuple(sorted(path for path in changed if _is_rebuildable(path)))
    unsafe_paths = tuple(sorted(changed - set(source_paths) - set(generated_paths)))
    if unsafe_paths:
        return _failure(
            "unsafe_worktree",
            "source or unknown local changes require manual review: "
            + ", ".join(unsafe_paths),
            source_paths=source_paths,
            unsafe_paths=unsafe_paths,
        )

    deleted_sources = tuple(sorted(set(deleted) & AUTHORITATIVE_RUNTIME_PATHS))
    if deleted_sources:
        return _failure(
            "raw_evidence_deleted",
            "authoritative runtime evidence was deleted; refusing to commit",
            source_paths=source_paths,
            unsafe_paths=deleted_sources,
        )

    tracked_generated = tuple(sorted(set(tracked) & set(generated_paths)))
    untracked_generated = tuple(sorted(set(untracked) & set(generated_paths)))
    archive_root = (
        repo_dir
        / "data_backup"
        / f"interrupted-analysis-{timestamp}"
    )
    archived: list[str] = []
    restored: list[str] = []

    try:
        for path in source_paths:
            valid, validation_error, source_archives = _validate_runtime_source(
                repo_dir,
                path,
                archive_root,
            )
            archived.extend(source_archives)
            if not valid:
                action = (
                    "raw_not_append_only"
                    if validation_error
                    and "not append-only" in validation_error
                    else "raw_validation_failed"
                )
                return _failure(
                    action,
                    validation_error or "runtime evidence validation failed",
                    source_paths=source_paths,
                    archived_paths=tuple(archived),
                )

        migration = runtime_paths.initialize_runtime_store(
            repo_dir,
            runtime_dir=runtime_dir,
            initialized_at=datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
        )
        if not migration.ok:
            return _failure(
                "runtime_migration_failed",
                "could not initialize runtime evidence store",
                source_paths=source_paths,
                archived_paths=tuple(archived),
            )
        for path in source_paths:
            merged, merge_error = _merge_source_into_runtime(
                repo_dir,
                path,
                runtime_dir,
            )
            if not merged:
                _archive_copy(repo_dir, path, archive_root)
                archived.append(path)
                return _failure(
                    "runtime_evidence_diverged",
                    merge_error or "runtime evidence diverged",
                    source_paths=source_paths,
                    archived_paths=tuple(archived),
                )

        if untracked_generated:
            for path in untracked_generated:
                _archive_untracked_output(repo_dir, path, archive_root)
                archived.append(path)

        if tracked_generated:
            for path in tracked_generated:
                if (
                    path in CANONICAL_VERSIONED_OUTPUT_PATHS
                    and (repo_dir / path).is_file()
                ):
                    _archive_copy(repo_dir, path, archive_root)
                    archived.append(path)
            restored_result = _run_git(
                repo_dir,
                "restore",
                "--source=HEAD",
                "--staged",
                "--worktree",
                "--",
                *tracked_generated,
            )
            if restored_result.returncode != 0:
                return _failure(
                    "generated_restore_failed",
                    restored_result.stderr or restored_result.stdout,
                    source_paths=source_paths,
                    archived_paths=tuple(archived),
                )
            restored.extend(tracked_generated)

        if source_paths:
            restored_sources = _run_git(
                repo_dir,
                "restore",
                "--source=HEAD",
                "--staged",
                "--worktree",
                "--",
                *source_paths,
            )
            if restored_sources.returncode != 0:
                return _failure(
                    "legacy_source_restore_failed",
                    restored_sources.stderr or restored_sources.stdout,
                    source_paths=source_paths,
                    restored_paths=tuple(restored),
                    archived_paths=tuple(archived),
                )
            restored.extend(source_paths)

        status = _run_git(repo_dir, "status", "--porcelain")
        if status.returncode != 0 or (status.stdout or "").strip():
            return _failure(
                "recovery_verification_failed",
                status.stderr or status.stdout or "worktree is still dirty",
                source_paths=source_paths,
                restored_paths=tuple(restored),
                archived_paths=tuple(archived),
            )
    except (OSError, ValueError) as exc:
        return _failure(
            "recovery_io_failed",
            str(exc),
            source_paths=source_paths,
            restored_paths=tuple(restored),
            archived_paths=tuple(archived),
        )

    repaired = bool(set(restored) - set(source_paths) or archived)
    migrated = bool(source_paths)
    if migrated and repaired:
        action = "migrated_and_repaired"
    elif migrated:
        action = "migrated"
    elif repaired:
        action = "repaired"
    else:
        action = "clean"
    return RecoveryResult(
        ok=True,
        action=action,
        source_paths=source_paths,
        restored_paths=tuple(restored),
        archived_paths=tuple(archived),
        commit=None,
    )
