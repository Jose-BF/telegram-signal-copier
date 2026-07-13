from __future__ import annotations

import copy
import hashlib
import json
import math
import platform
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
RUNTIME_PACKAGES = ("pandas", "numpy", "pyarrow")
_TICK_CONTRACT_FIELDS = (
    "tick_time_contract",
    "time_basis",
    "parquet_sha256",
    "size_bytes",
)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]

    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return _json_safe(item_method())
        except (TypeError, ValueError):
            pass
    return str(value)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _portable_path(path: Path, repo_dir: Path) -> str:
    resolved_path = Path(path).resolve()
    resolved_repo = Path(repo_dir).resolve()
    try:
        return resolved_path.relative_to(resolved_repo).as_posix()
    except ValueError:
        return resolved_path.name


def _file_record(role: str, path: Path, repo_dir: Path) -> dict[str, Any]:
    resolved = Path(path)
    return {
        "role": role,
        "path": _portable_path(resolved, repo_dir),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def runtime_versions() -> dict[str, Any]:
    packages: dict[str, str | None] = {}
    for package in RUNTIME_PACKAGES:
        try:
            packages[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            packages[package] = None
    return {
        "python": platform.python_version(),
        "packages": packages,
    }


def _git_command(repo_dir: Path, *args: str) -> tuple[str | None, str | None]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=Path(repo_dir),
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git_{args[0]}:{type(exc).__name__}"
    if completed.returncode:
        return None, f"git_{args[0]}:exit_{completed.returncode}"
    return completed.stdout.strip(), None


def git_diagnostics(repo_dir: Path) -> dict[str, Any]:
    commit, commit_error = _git_command(repo_dir, "rev-parse", "HEAD")
    branch, branch_error = _git_command(repo_dir, "branch", "--show-current")
    status, status_error = _git_command(repo_dir, "status", "--porcelain")
    errors = [
        error
        for error in (commit_error, branch_error, status_error)
        if error is not None
    ]
    return {
        "commit": commit,
        "branch": branch or None,
        "dirty": None if status is None else bool(status),
        "errors": errors,
    }


def result_fingerprint(report: Mapping[str, Any]) -> str:
    semantic_report = copy.deepcopy(dict(report))
    semantic_report.pop("generated_at", None)
    semantic_report.pop("provenance", None)
    return sha256_json(semantic_report)


def _payload_records(
    selected_payloads: Mapping[str, Sequence[Any]],
) -> list[dict[str, Any]]:
    records = []
    for role in sorted(selected_payloads):
        payload = list(selected_payloads[role])
        records.append({
            "role": role,
            "count": len(payload),
            "sha256": sha256_json(payload),
        })
    return records


def _policy_record(policies: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = [dict(policy) for policy in policies]
    return {
        "count": len(ordered),
        "sha256": sha256_json(ordered),
    }


def _tick_record(day: str, contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "day": day,
        **{field: _json_safe(contract[field]) for field in _TICK_CONTRACT_FIELDS},
    }


def result_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    canonical_scope = report.get("canonical_scope") or {}
    selection = report.get("selection") or {}
    return {
        "provider_signals": canonical_scope.get("provider_signals"),
        "executed_trades": report.get("executed_trade_count"),
        "policy_count": report.get("policy_count"),
        "selected_policy": selection.get("selected_policy"),
        "selection_blockers": list(selection.get("global_blockers") or []),
    }


def build_run_evidence(
    *,
    repo_dir: Path,
    report: Mapping[str, Any],
    parameters: Mapping[str, Any],
    selected_payloads: Mapping[str, Sequence[Any]],
    policies: Sequence[Mapping[str, Any]],
    input_files: Mapping[str, Path],
    source_files: Mapping[str, Path],
    required_tick_days: Sequence[str],
    tick_contracts: Mapping[str, Mapping[str, Any]],
    runtime: Mapping[str, Any] | None = None,
    git: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    input_records: list[dict[str, Any]] = []
    for role in sorted(input_files):
        path = Path(input_files[role])
        if not path.is_file():
            errors.append(f"missing_input:{role}")
            continue
        input_records.append(_file_record(role, path, repo_dir))

    source_records: list[dict[str, Any]] = []
    for role in sorted(source_files):
        path = Path(source_files[role])
        if not path.is_file():
            errors.append(f"missing_source:{role}")
            continue
        source_records.append(_file_record(role, path, repo_dir))

    tick_records: list[dict[str, Any]] = []
    tick_days = sorted(set(required_tick_days))
    for day in tick_days:
        contract = tick_contracts.get(day)
        if contract is None or any(
            field not in contract for field in _TICK_CONTRACT_FIELDS
        ):
            errors.append(f"unverified_tick_contract:{day}")
            continue
        tick_records.append(_tick_record(day, contract))

    runtime_record = _json_safe(runtime if runtime is not None else runtime_versions())
    git_record = _json_safe(git if git is not None else git_diagnostics(repo_dir))
    parameter_record = _json_safe(parameters)
    selected_input_records = _payload_records(selected_payloads)
    policy_record = _policy_record(policies)

    source_identity = [
        {"role": record["role"], "sha256": record["sha256"]}
        for record in source_records
    ]
    identity = {
        "schema_version": SCHEMA_VERSION,
        "parameters": parameter_record,
        "selected_inputs": selected_input_records,
        "policies": policy_record,
        "source_files": source_identity,
        "runtime": runtime_record,
        "tick_days": tick_records,
    }
    limitations = ["tick_artifacts_local_cache_only"] if tick_days else []

    return {
        "schema_version": SCHEMA_VERSION,
        "run_fingerprint": sha256_json(identity),
        "result_fingerprint": result_fingerprint(report),
        "reproducibility": {
            "verified_now": not errors,
            "durable": not tick_days,
            "errors": errors,
            "limitations": limitations,
            "git": git_record,
            "runtime": runtime_record,
            "parameters": parameter_record,
            "selected_inputs": selected_input_records,
            "policies": policy_record,
            "input_artifacts": input_records,
            "source_files": source_records,
            "tick_days": tick_records,
        },
        "result_summary": result_summary(report),
    }
