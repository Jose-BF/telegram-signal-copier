from __future__ import annotations

import copy
import hashlib
import json
import math
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 3
RUNTIME_PACKAGES = ("pandas", "numpy", "pyarrow")
_PROVIDER_FIRST_PAYLOAD_ROLES = {
    "provider_scope",
    "provider_trade_specs",
    "provider_latency_scenarios_ms",
    "provider_volume_per_leg",
    "provider_policy_results",
}
_TICK_CONTRACT_FIELDS = (
    "tick_time_contract",
    "time_basis",
    "source_time_basis",
    "utc_offset_seconds",
    "offset_detection_method",
    "offset_reference",
    "semantic_time_valid",
    "anchor_validation",
    "parquet_sha256",
    "size_bytes",
)


class ProvenanceConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicationResult:
    report: dict[str, Any]
    status: str
    run_dir: Path | None
    idempotent: bool


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
    semantic_report.pop("validation", None)
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


def _identity_payload(
    schema_version: Any,
    reproducibility: Mapping[str, Any],
) -> dict[str, Any]:
    source_identity = [
        {
            "role": record["role"],
            "sha256": record["sha256"],
        }
        for record in reproducibility.get("source_files") or []
    ]
    return {
        "schema_version": schema_version,
        "parameters": reproducibility.get("parameters"),
        "selected_inputs": reproducibility.get("selected_inputs"),
        "policies": reproducibility.get("policies"),
        "source_files": source_identity,
        "runtime": reproducibility.get("runtime"),
        "tick_days": reproducibility.get("tick_days"),
        "market_replay": reproducibility.get("market_replay"),
    }


def _verified_tick_contract(contract: Mapping[str, Any]) -> bool:
    anchor_validation = contract.get("anchor_validation")
    offset = contract.get("utc_offset_seconds")
    digest = str(contract.get("parquet_sha256") or "")
    size = contract.get("size_bytes")
    return (
        contract.get("tick_time_contract") == "mt5_server_epoch_utc_v3"
        and contract.get("time_basis") == "UTC"
        and contract.get("source_time_basis") == "mt5_server_epoch"
        and not isinstance(offset, bool)
        and isinstance(offset, int)
        and abs(offset) <= 14 * 3600
        and bool(contract.get("offset_detection_method"))
        and isinstance(contract.get("offset_reference"), Mapping)
        and contract.get("semantic_time_valid") is True
        and isinstance(anchor_validation, Mapping)
        and anchor_validation.get("valid") is True
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
        and not isinstance(size, bool)
        and isinstance(size, int)
        and size > 0
    )


def _provider_first_mode(report: Mapping[str, Any]) -> bool:
    validation = report.get("validation")
    return (
        isinstance(validation, Mapping)
        and validation.get("price_path_mode") == "provider_first"
    )


def _provider_row_accounting_verified(report: Mapping[str, Any]) -> bool:
    if not _provider_first_mode(report):
        return True
    scope = report.get("provider_scope")
    if not isinstance(scope, Mapping):
        return False
    values = {
        key: scope.get(key)
        for key in (
            "formal_signals",
            "policy_count",
            "rows_expected",
            "rows_emitted",
            "simulated_rows",
            "blocked_rows",
        )
    }
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in values.values()
    ):
        return False
    latencies = scope.get("latency_scenarios_ms")
    omitted = scope.get("signals_omitted")
    if (
        not isinstance(latencies, list)
        or not latencies
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in latencies
        )
        or len(set(latencies)) != len(latencies)
        or not isinstance(omitted, list)
        or omitted
    ):
        return False
    expected = (
        values["formal_signals"]
        * values["policy_count"]
        * len(latencies)
    )
    return (
        values["rows_expected"] == expected
        and values["rows_emitted"] == expected
        and values["simulated_rows"] + values["blocked_rows"] == expected
        and report.get("policy_count") == values["policy_count"]
    )


def _normalize_market_replay(value: Mapping[str, Any]) -> dict[str, int]:
    normalized = {
        key: int(value.get(key) or 0)
        for key in ("selected_trades", "exact", "blocked", "mismatched")
    }
    if any(count < 0 for count in normalized.values()):
        raise ValueError("market replay counts cannot be negative")
    return normalized


def _market_replay_verified(summary: Mapping[str, int]) -> bool:
    selected = int(summary.get("selected_trades") or 0)
    return (
        selected > 0
        and int(summary.get("exact") or 0) == selected
        and int(summary.get("blocked") or 0) == 0
        and int(summary.get("mismatched") or 0) == 0
    )


def result_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    canonical_scope = report.get("canonical_scope") or {}
    provider_scope = report.get("provider_scope") or {}
    selection = report.get("selection") or {}
    return {
        "provider_signals": provider_scope.get(
            "formal_signals",
            canonical_scope.get("provider_signals"),
        ),
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
    market_replay: Mapping[str, Any],
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
        ) or not _verified_tick_contract(contract):
            errors.append(f"unverified_tick_contract:{day}")
            continue
        tick_records.append(_tick_record(day, contract))

    runtime_record = _json_safe(runtime if runtime is not None else runtime_versions())
    git_record = _json_safe(git if git is not None else git_diagnostics(repo_dir))
    parameter_record = _json_safe(parameters)
    selected_input_records = _payload_records(selected_payloads)
    provider_first = _provider_first_mode(report)
    if provider_first:
        for role in sorted(_PROVIDER_FIRST_PAYLOAD_ROLES - set(selected_payloads)):
            errors.append(f"missing_provider_selected_payload:{role}")
    provider_row_accounting_verified = _provider_row_accounting_verified(report)
    if provider_first and not provider_row_accounting_verified:
        errors.append("provider_row_accounting_incomplete")
    policy_record = _policy_record(policies)
    market_replay_record = _normalize_market_replay(market_replay)

    limitations = ["tick_artifacts_local_cache_only"] if tick_days else []
    report_validation = report.get("validation") or {}
    money_mode = (
        str(report_validation.get("money_mode") or "diagnostic_only")
        if provider_first
        else "legacy_verified"
    )
    money_contract_verified = (
        not provider_first or money_mode == "verified_account_currency"
    )
    if provider_first and not money_contract_verified:
        limitations.append("broker_money_contract_unverified")
    reproducibility = {
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
        "market_replay": market_replay_record,
    }
    artifact_integrity_verified = not errors
    market_replay_verified = _market_replay_verified(market_replay_record)
    conclusions_allowed = (
        artifact_integrity_verified
        and market_replay_verified
        and provider_row_accounting_verified
        and money_contract_verified
    )
    validation = {
        "artifact_integrity_verified": artifact_integrity_verified,
        "market_replay_verified": market_replay_verified,
        "conclusions_allowed": conclusions_allowed,
        "mode": (
            "verified_simulation" if conclusions_allowed
            else "diagnostic_only"
        ),
        "market_replay": market_replay_record,
    }
    if provider_first:
        validation.update({
            "price_path_mode": "provider_first",
            "money_mode": money_mode,
            "provider_row_accounting_verified": (
                provider_row_accounting_verified
            ),
            "money_contract_verified": money_contract_verified,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "run_fingerprint": sha256_json(
            _identity_payload(SCHEMA_VERSION, reproducibility)
        ),
        "result_fingerprint": result_fingerprint(report),
        "reproducibility": reproducibility,
        "validation": validation,
        "result_summary": result_summary(report),
    }


def pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _provenance_ref(
    evidence: Mapping[str, Any],
    status: str,
    card_path: str | None,
) -> dict[str, Any]:
    reproducibility = evidence["reproducibility"]
    validation = evidence["validation"]
    return {
        "status": status,
        "run_fingerprint": evidence["run_fingerprint"],
        "result_fingerprint": evidence["result_fingerprint"],
        "run_card": card_path,
        "verified_now": reproducibility["verified_now"],
        "durable": reproducibility["durable"],
        "errors": list(reproducibility["errors"]),
        "limitations": list(reproducibility["limitations"]),
        "mode": validation["mode"],
        "conclusions_allowed": validation["conclusions_allowed"],
    }


def _retained_artifact_path(run_dir: Path, relative_path: str) -> Path:
    artifact_path = (run_dir / relative_path).resolve()
    try:
        artifact_path.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise ProvenanceConflictError(
            f"invalid_retained_artifact_path:{relative_path}"
        ) from exc
    return artifact_path


def _validate_existing(
    run_dir: Path,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    card_path = run_dir / "run_card.json"
    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvenanceConflictError(
            f"invalid_existing_run_card:{type(exc).__name__}"
        ) from exc

    for key in ("schema_version", "run_fingerprint", "result_fingerprint"):
        if card.get(key) != evidence.get(key):
            raise ProvenanceConflictError(f"existing_{key}_mismatch")
    if card.get("validation") != evidence.get("validation"):
        raise ProvenanceConflictError("existing_validation_mismatch")

    card_reproducibility = card.get("reproducibility")
    if not isinstance(card_reproducibility, Mapping):
        raise ProvenanceConflictError("invalid_existing_reproducibility")
    try:
        derived_fingerprint = sha256_json(
            _identity_payload(card.get("schema_version"), card_reproducibility)
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProvenanceConflictError(
            "invalid_existing_computational_identity"
        ) from exc
    if derived_fingerprint != card.get("run_fingerprint"):
        raise ProvenanceConflictError("existing_computational_identity_mismatch")
    if not card_reproducibility.get("verified_now"):
        raise ProvenanceConflictError("existing_run_not_verified")

    artifacts = card.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ProvenanceConflictError("invalid_existing_artifacts")
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise ProvenanceConflictError("invalid_existing_artifact_record")
        if not artifact.get("retained"):
            continue
        relative_path = str(artifact.get("path") or "")
        path = _retained_artifact_path(run_dir, relative_path)
        if not path.is_file():
            raise ProvenanceConflictError(
                f"missing_retained_artifact:{relative_path}"
            )
        try:
            expected_size = int(artifact["size_bytes"])
            expected_hash = str(artifact["sha256"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProvenanceConflictError(
                f"invalid_retained_artifact_record:{relative_path}"
            ) from exc
        if path.stat().st_size != expected_size:
            raise ProvenanceConflictError(
                f"artifact_size_mismatch:{relative_path}"
            )
        if sha256_file(path) != expected_hash:
            raise ProvenanceConflictError(
                f"artifact_hash_mismatch:{relative_path}"
            )
        try:
            retained_report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProvenanceConflictError(
                f"invalid_retained_artifact_json:{relative_path}"
            ) from exc
        if result_fingerprint(retained_report) != card.get("result_fingerprint"):
            raise ProvenanceConflictError(
                f"artifact_result_mismatch:{relative_path}"
            )

    return card


def _apply_first_report_metadata(
    report: Mapping[str, Any],
    card: Mapping[str, Any],
) -> dict[str, Any]:
    metadata = card.get("report_metadata")
    if not isinstance(metadata, Mapping):
        raise ProvenanceConflictError("invalid_existing_report_metadata")
    provenance = metadata.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ProvenanceConflictError("invalid_existing_report_provenance")
    validation = metadata.get("validation")
    if not isinstance(validation, Mapping):
        raise ProvenanceConflictError("invalid_existing_report_validation")

    latest = copy.deepcopy(dict(report))
    if "generated_at" in metadata:
        latest["generated_at"] = metadata["generated_at"]
    else:
        latest.pop("generated_at", None)
    latest["provenance"] = copy.deepcopy(dict(provenance))
    latest["validation"] = copy.deepcopy(dict(validation))
    return latest


def _validate_report_bytes(
    card: Mapping[str, Any],
    report_bytes: bytes,
    *,
    include_trades: bool,
) -> None:
    artifacts = card.get("artifacts") or []
    if len(artifacts) != 1 or not isinstance(artifacts[0], Mapping):
        raise ProvenanceConflictError("invalid_existing_report_artifact")
    artifact = artifacts[0]
    if bool(artifact.get("retained")) == include_trades:
        raise ProvenanceConflictError("existing_artifact_retention_mismatch")
    try:
        expected_size = int(artifact.get("size_bytes") or -1)
    except (TypeError, ValueError) as exc:
        raise ProvenanceConflictError(
            "invalid_existing_report_artifact_size"
        ) from exc
    if expected_size != len(report_bytes):
        raise ProvenanceConflictError("existing_report_size_mismatch")
    if str(artifact.get("sha256")) != hashlib.sha256(report_bytes).hexdigest():
        raise ProvenanceConflictError("existing_report_hash_mismatch")


def _validate_fingerprint(value: Any, role: str) -> str:
    fingerprint = str(value)
    if len(fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in fingerprint
    ):
        raise ProvenanceConflictError(f"invalid_{role}_fingerprint")
    return fingerprint


def publish_run_archive(
    *,
    report: Mapping[str, Any],
    evidence: Mapping[str, Any],
    archive_root: Path,
    output_path: Path,
    include_trades: bool,
    repo_dir: Path,
    now: datetime | None = None,
) -> PublicationResult:
    run_fingerprint = _validate_fingerprint(
        evidence.get("run_fingerprint"),
        "run",
    )
    expected_result = _validate_fingerprint(
        evidence.get("result_fingerprint"),
        "result",
    )
    if result_fingerprint(report) != expected_result:
        raise ProvenanceConflictError("evidence_result_fingerprint_mismatch")
    reproducibility = evidence.get("reproducibility")
    if not isinstance(reproducibility, Mapping):
        raise ProvenanceConflictError("invalid_reproducibility")
    validation = evidence.get("validation")
    if not isinstance(validation, Mapping):
        raise ProvenanceConflictError("invalid_validation")
    if bool(validation.get("artifact_integrity_verified")) != bool(
        reproducibility.get("verified_now")
    ):
        raise ProvenanceConflictError("validation_integrity_mismatch")
    expected_conclusions = bool(
        validation.get("artifact_integrity_verified")
        and validation.get("market_replay_verified")
        and validation.get("provider_row_accounting_verified", True)
        and validation.get("money_contract_verified", True)
    )
    if bool(validation.get("conclusions_allowed")) != expected_conclusions:
        raise ProvenanceConflictError("validation_conclusions_mismatch")
    expected_mode = (
        "verified_simulation" if expected_conclusions else "diagnostic_only"
    )
    if validation.get("mode") != expected_mode:
        raise ProvenanceConflictError("validation_mode_mismatch")
    derived_run_fingerprint = sha256_json(
        _identity_payload(evidence.get("schema_version"), reproducibility)
    )
    if derived_run_fingerprint != run_fingerprint:
        raise ProvenanceConflictError("evidence_run_fingerprint_mismatch")

    latest = copy.deepcopy(dict(report))
    latest["validation"] = copy.deepcopy(dict(validation))
    if not reproducibility["verified_now"]:
        latest["provenance"] = _provenance_ref(evidence, "incomplete", None)
        return PublicationResult(latest, "incomplete", None, False)

    publication_status = (
        "archived" if validation["conclusions_allowed"]
        else "diagnostic_archived"
    )
    archive_root = Path(archive_root)
    run_dir = archive_root / run_fingerprint
    card_path = run_dir / "run_card.json"
    card_ref = _portable_path(card_path, Path(repo_dir))
    latest["provenance"] = _provenance_ref(
        evidence,
        publication_status,
        card_ref,
    )

    if run_dir.exists():
        existing_card = _validate_existing(run_dir, evidence)
        latest = _apply_first_report_metadata(report, existing_card)
        report_bytes = pretty_json_bytes(latest)
        _validate_report_bytes(
            existing_card,
            report_bytes,
            include_trades=include_trades,
        )
        return PublicationResult(latest, publication_status, run_dir, True)

    report_bytes = pretty_json_bytes(latest)

    if include_trades:
        artifacts = [{
            "path": _portable_path(Path(output_path), Path(repo_dir)),
            "size_bytes": len(report_bytes),
            "sha256": hashlib.sha256(report_bytes).hexdigest(),
            "retained": False,
        }]
    else:
        artifacts = [{
            "path": "strategy_farm.json",
            "size_bytes": len(report_bytes),
            "sha256": hashlib.sha256(report_bytes).hexdigest(),
            "retained": True,
        }]

    created_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    card = {
        **copy.deepcopy(dict(evidence)),
        "created_at_utc": created_at,
        "report_metadata": {
            "generated_at": latest.get("generated_at"),
            "provenance": copy.deepcopy(latest["provenance"]),
            "validation": copy.deepcopy(latest["validation"]),
        },
        "artifacts": artifacts,
    }
    archive_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".tmp-run-", dir=archive_root))
    idempotent = False
    try:
        (temp_dir / "run_card.json").write_bytes(pretty_json_bytes(card))
        if not include_trades:
            (temp_dir / "strategy_farm.json").write_bytes(report_bytes)
        try:
            temp_dir.replace(run_dir)
        except OSError:
            if not run_dir.exists():
                raise
            existing_card = _validate_existing(run_dir, evidence)
            latest = _apply_first_report_metadata(report, existing_card)
            report_bytes = pretty_json_bytes(latest)
            _validate_report_bytes(
                existing_card,
                report_bytes,
                include_trades=include_trades,
            )
            idempotent = True
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

    return PublicationResult(latest, publication_status, run_dir, idempotent)
