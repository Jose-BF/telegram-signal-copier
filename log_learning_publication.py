"""Authoritative freshness status for recursive learning artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping

import recursive_log_learning as learning


STATUS_SCHEMA_VERSION = 1


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _load_json_bytes(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value, raw


def _read_repository_state(root: Path) -> dict:
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

    head = run("rev-parse", "HEAD")
    status = run("status", "--porcelain", "--untracked-files=all")
    if head.returncode != 0 or status.returncode != 0:
        detail = head.stderr or status.stderr or "git state unavailable"
        raise ValueError(detail.strip())

    dirty_rows = [
        line for line in (status.stdout or "").splitlines() if line.strip()
    ]
    changed_paths = []
    for row in dirty_rows:
        path = row[3:].strip() if len(row) > 3 else row.strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        changed_paths.append(path.strip('"').replace("\\", "/"))
    return {
        "git_commit": head.stdout.strip(),
        "git_dirty": bool(dirty_rows),
        "source_dirty": any(
            not path.startswith("data/") for path in changed_paths
        ),
    }


def _expected_learning_bytes(root: Path) -> tuple[bytes, bytes]:
    outputs = learning.build_repository_learning_outputs(root)
    return outputs.report_bytes, outputs.registry_bytes


def _artifact_identity(
    report_path: Path,
    registry_path: Path,
    *,
    repo_root: Path,
    git_commit: str,
    dependencies: dict[str, bool],
) -> dict:
    report, report_bytes = _load_json_bytes(report_path)
    registry, registry_bytes = _load_json_bytes(registry_path)

    report_sources = (report.get("corpus") or {}).get("source_fingerprints")
    registry_sources = registry.get("source_fingerprints")
    if not isinstance(report_sources, dict) or not isinstance(
        registry_sources, dict,
    ):
        raise ValueError("learning artifacts lack source fingerprints")
    if report_sources != registry_sources:
        raise ValueError("report and registry source fingerprints differ")

    registry_sha256 = _sha256_bytes(registry_bytes)
    if report.get("registry_fingerprint") != registry_sha256:
        raise ValueError("report registry fingerprint does not match registry bytes")

    expected_report, expected_registry = _expected_learning_bytes(repo_root)
    if report_bytes != expected_report or registry_bytes != expected_registry:
        raise ValueError(
            "learning artifacts do not match the current repository corpus"
        )

    evidence_sources = {
        key: value
        for key, value in registry_sources.items()
        if key != "review_metadata"
    }
    review_fingerprint = registry_sources.get("review_metadata")
    if not evidence_sources or not isinstance(review_fingerprint, str):
        raise ValueError("learning artifacts lack separated review fingerprint")

    report_sha256 = _sha256_bytes(report_bytes)
    source_fingerprint = _fingerprint(evidence_sources)
    publication_id = _fingerprint({
        "dependencies": dependencies,
        "git_commit": git_commit,
        "registry_sha256": registry_sha256,
        "report_sha256": report_sha256,
        "review_fingerprint": review_fingerprint,
        "source_fingerprint": source_fingerprint,
    })
    return {
        "report": report,
        "report_sha256": report_sha256,
        "registry_sha256": registry_sha256,
        "source_fingerprint": source_fingerprint,
        "review_fingerprint": review_fingerprint,
        "publication_id": publication_id,
    }


def publish_status(
    *,
    status_path: Path,
    report_path: Path,
    registry_path: Path,
    repo_root: Path,
    dependencies: Mapping[str, bool],
    build_returncode: int | None,
    attempted_at_utc: str,
    error: str | None = None,
) -> dict:
    """Write and return the authoritative result of one publication attempt."""
    normalized_dependencies = {
        str(name): value is True
        for name, value in sorted(dependencies.items())
    }
    blockers: list[str] = [
        f"dependency_failed:{name}"
        for name, passed in normalized_dependencies.items()
        if not passed
    ]
    try:
        repository = _read_repository_state(repo_root)
    except (OSError, ValueError) as exc:
        repository = {
            "git_commit": None,
            "git_dirty": True,
            "source_dirty": True,
        }
        blockers.append(f"repository_state_invalid:{exc}")
    if repository["source_dirty"]:
        blockers.append("uncommitted_source_changes")

    build_succeeded = build_returncode == 0
    status = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "attempted_at_utc": attempted_at_utc,
        "ok": False,
        "fresh": False,
        "build_succeeded": build_succeeded,
        "artifacts_valid": False,
        "build_returncode": build_returncode,
        "dependencies": normalized_dependencies,
        "git_commit": repository["git_commit"],
        "git_dirty": repository["git_dirty"],
        "source_dirty": repository["source_dirty"],
        "source_fingerprint": None,
        "review_fingerprint": None,
        "report_sha256": None,
        "registry_sha256": None,
        "publication_id": None,
        "latest_evidence_utc": None,
        "strategy_blockers": [],
        "blockers": blockers,
        "conclusions_allowed": False,
        "error": error,
    }

    if not build_succeeded:
        suffix = (
            str(build_returncode)
            if build_returncode is not None
            else "exception"
        )
        status["blockers"].append(f"learning_build_failed:{suffix}")
        status["blockers"].sort()
        _atomic_write(status_path, status)
        return status

    try:
        identity = _artifact_identity(
            report_path,
            registry_path,
            repo_root=repo_root,
            git_commit=str(repository["git_commit"] or "unknown"),
            dependencies=normalized_dependencies,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        status["blockers"].append(f"artifacts_invalid:{exc}")
        status["blockers"].sort()
        _atomic_write(status_path, status)
        return status

    report = identity.pop("report")
    status.update(identity)
    status["artifacts_valid"] = True
    status["latest_evidence_utc"] = (
        report.get("corpus") or {}
    ).get("latest_evidence_utc")
    status["strategy_blockers"] = sorted(
        str(value) for value in (report.get("hard_gate_blockers") or [])
    )
    status["ok"] = not status["blockers"]
    status["fresh"] = status["ok"]
    status["conclusions_allowed"] = bool(
        status["ok"] and report.get("safe_for_strategy_simulation") is True
    )
    _atomic_write(status_path, status)
    return status
