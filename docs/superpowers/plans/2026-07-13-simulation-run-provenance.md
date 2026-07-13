# Simulation Run Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add compact, content-addressed evidence for every strategy-farm run so identical computational inputs are repeatable, contradictory results fail closed, and the exact provider catalog used on the VM reaches GitHub.

**Architecture:** Keep the causal simulator unchanged. Extend its existing tick loader to expose contracts it already verifies, build deterministic evidence in a standard-library-only module, and publish immutable run cards only from the `strategy_farm.py` CLI. The live Telegram/MT5 process never imports or invokes the new module; the watcher only runs and stages it after a bot session.

**Tech Stack:** Python 3.14, standard-library `hashlib/json/pathlib/tempfile/subprocess/importlib.metadata`, existing pandas/NumPy/PyArrow runtime, pytest, Git.

---

### Task 0: Confirm the Isolated Baseline

**Files:**
- Verify only: complete repository

- [ ] **Step 1: Confirm branch and worktree state**

Run:

```powershell
git status -sb
git log -2 --oneline --decorate
```

Expected: branch `feat/replay-validator`, tracking `origin/main`, with only the
committed provenance design/plan ahead of production.

- [ ] **Step 2: Run the complete baseline suite**

Run:

```powershell
python -m pytest -q
```

Expected: the existing suite passes with the single optional Gemini skip. Stop
and diagnose any baseline failure before changing implementation files.

### Task 1: Expose Already-Verified Tick Contracts

**Files:**
- Modify: `tools/ensure_replay_tick_cache.py`
- Modify: `observed_tick_replay_validator.py`
- Test: `tests/test_ensure_replay_tick_cache.py`
- Test: `tests/test_observed_tick_replay_validator.py`

- [ ] **Step 1: Write the failing normalized-contract test**

Add a test that writes a small Parquet day plus its sidecar and expects one
normalized record rather than a boolean-only result:

```python
def test_load_valid_day_contract_returns_normalized_evidence(tmp_path):
    cache_dir = tmp_path / "ticks"
    cache_dir.mkdir()
    day = date(2026, 7, 6)
    parquet = cache_dir / "2026-07-06.parquet"
    pd.DataFrame([{
        "time_msc": 1783324800000,
        "bid": 4200.0,
        "ask": 4200.2,
        "time_utc": "2026-07-06T00:00:00+00:00",
    }]).to_parquet(parquet, index=False)
    ensure_replay_tick_cache.write_day_contract(cache_dir, day)

    record = ensure_replay_tick_cache.load_valid_day_contract(cache_dir, day)

    assert record == {
        "day": "2026-07-06",
        "tick_time_contract": "mt5_utc_v2",
        "time_basis": "UTC",
        "parquet_sha256": ensure_replay_tick_cache._file_sha256(parquet),
        "size_bytes": parquet.stat().st_size,
    }
```

- [ ] **Step 2: Run the targeted test and verify RED**

Run:

```powershell
python -m pytest tests/test_ensure_replay_tick_cache.py -q
```

Expected: FAIL because `load_valid_day_contract` does not exist.

- [ ] **Step 3: Implement one source of truth for contract validation**

Add this function and make `day_contract_valid()` a thin wrapper:

```python
def load_valid_day_contract(cache_dir: Path, day: date) -> dict | None:
    parquet_path = _day_file(cache_dir, day)
    contract_path = _day_contract_file(cache_dir, day)
    if not parquet_path.is_file() or not contract_path.is_file():
        return None
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    digest = _file_sha256(parquet_path)
    if not (
        contract.get("tick_time_contract") == TICK_TIME_CONTRACT
        and contract.get("time_basis") == "UTC"
        and contract.get("parquet_sha256") == digest
    ):
        return None
    return {
        "day": day.isoformat(),
        "tick_time_contract": TICK_TIME_CONTRACT,
        "time_basis": "UTC",
        "parquet_sha256": digest,
        "size_bytes": parquet_path.stat().st_size,
    }


def day_contract_valid(cache_dir: Path, day: date) -> bool:
    return load_valid_day_contract(cache_dir, day) is not None
```

- [ ] **Step 4: Write the failing loader-evidence test**

Add a test that loads two trades from one verified day and proves validation is
recorded once while the required-day set remains complete:

```python
def test_tick_loader_exposes_verified_contracts_and_required_days(
    tmp_path, monkeypatch,
):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    parquet = cache_dir / "2026-07-06.parquet"
    _ticks([{
        "time_utc": "2026-07-06T10:00:00+00:00",
        "bid": 4199.8,
        "ask": 4200.0,
    }]).to_parquet(parquet, index=False)
    ensure_replay_tick_cache.write_day_contract(cache_dir, date(2026, 7, 6))

    loader = observed_tick_replay_validator.ReplayTickFrameCache(cache_dir)
    loader.load_ticks_for_trade(_trade(sig_id="canal1_1"))
    loader.load_ticks_for_trade(_trade(sig_id="canal1_2"))

    assert loader.required_days == ["2026-07-06"]
    assert loader.verified_contracts["2026-07-06"]["parquet_sha256"]
    assert loader.verified_contracts["2026-07-06"]["size_bytes"] == parquet.stat().st_size
```

- [ ] **Step 5: Implement read-only evidence properties**

Update `ReplayTickFrameCache` without changing the returned tick frames:

```python
class ReplayTickFrameCache:
    def __init__(self, tick_cache_dir: Path):
        self.tick_cache_dir = Path(tick_cache_dir)
        self._frames: dict[str, pd.DataFrame] = {}
        self._required_days: set[str] = set()
        self._verified_contracts: dict[str, dict] = {}

    @property
    def required_days(self) -> list[str]:
        return sorted(self._required_days)

    @property
    def verified_contracts(self) -> dict[str, dict]:
        return {
            day: dict(record)
            for day, record in sorted(self._verified_contracts.items())
        }

    def _load_day(self, day: str) -> tuple[pd.DataFrame | None, str | None]:
        if day in self._frames:
            return self._frames[day], None
        path = self.tick_cache_dir / f"{day}.parquet"
        contract = ensure_replay_tick_cache.load_valid_day_contract(
            self.tick_cache_dir,
            datetime.fromisoformat(day).date(),
        )
        if contract is None:
            reason = "invalid_tick_cache_contract" if path.exists() else "missing_tick_cache"
            return None, f"{reason}:{day}"
        self._verified_contracts[day] = contract
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:
            return None, f"tick_cache_read_failed:{day}:{type(exc).__name__}"
        if not frame.empty:
            frame = frame.copy()
            frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
            frame = frame.sort_values("time_utc").reset_index(drop=True)
        self._frames[day] = frame
        return frame, None

    def load_ticks_for_trade(
        self,
        trade: dict,
        *,
        pad_minutes: int = 5,
    ) -> tuple[pd.DataFrame, list[str]]:
        missing: list[str] = []
        frames: list[pd.DataFrame] = []
        for day in _required_tick_days(trade, pad_minutes):
            self._required_days.add(day)
            frame, error = self._load_day(day)
            if error:
                missing.append(error)
                continue
            if frame is not None and not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(), missing
        ticks = pd.concat(frames, ignore_index=True).sort_values("time_utc")
        return ticks.reset_index(drop=True), missing
```

- [ ] **Step 6: Run targeted tests and commit**

Run:

```powershell
python -m pytest tests/test_ensure_replay_tick_cache.py tests/test_observed_tick_replay_validator.py -q
git add tools/ensure_replay_tick_cache.py observed_tick_replay_validator.py tests/test_ensure_replay_tick_cache.py tests/test_observed_tick_replay_validator.py
git commit -m "replay: expose verified tick contract evidence"
```

Expected: targeted tests PASS and no tick selection behavior changes.

### Task 2: Build Deterministic Run Evidence

**Files:**
- Create: `simulation_run_provenance.py`
- Create: `tests/test_simulation_run_provenance.py`

- [ ] **Step 1: Write failing canonical-identity tests**

Create tests covering stable mapping order, preserved list order, non-semantic
fields, runtime/source/tick changes and secret exclusion:

```python
import copy
import json
from pathlib import Path

import pytest

import simulation_run_provenance as provenance


def _report(generated_at="2026-07-13T10:00:00+00:00"):
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "from_date": "2026-07-06",
        "to_date": None,
        "executed_trade_count": 2,
        "policy_count": 1,
        "includes_trade_details": False,
        "canonical_scope": {"provider_signals": 2},
        "selection": {
            "selected_policy": None,
            "global_blockers": ["oos_not_validated"],
        },
        "policies": [{
            "policy_id": "follow_actual",
            "metrics": {"net_pnl": 1.0},
        }],
    }


def _evidence_fixture(root: Path, branch="main"):
    root.mkdir(parents=True, exist_ok=True)
    input_dir = root / "inputs"
    source_dir = root / "source"
    input_dir.mkdir()
    source_dir.mkdir()
    replay = input_dir / "replay.jsonl"
    baseline = input_dir / "baseline.jsonl"
    catalog = input_dir / "catalog.json"
    engine = source_dir / "engine.py"
    replay.write_text('{"sig_id":"canal1_2"}\n{"sig_id":"canal1_1"}\n', encoding="utf-8")
    baseline.write_text('{"sig_id":"canal1_1","status":"exact"}\n', encoding="utf-8")
    catalog.write_text('{"signals":[]}\n', encoding="utf-8")
    engine.write_text("ENGINE_VERSION = 1\n", encoding="utf-8")
    return {
        "repo_dir": root,
        "report": _report(),
        "parameters": {"from_date": "2026-07-06", "to_date": None},
        "selected_payloads": {
            "replay_trades": [
                {"sig_id": "canal1_2"},
                {"sig_id": "canal1_1"},
            ],
            "effective_baselines": [
                {"sig_id": "canal1_2", "baseline": None},
                {"sig_id": "canal1_1", "baseline": {"status": "exact"}},
            ],
        },
        "policies": [{"policy_id": "follow_actual", "mode": "follow_actual"}],
        "input_files": {
            "replay_trades": replay,
            "observed_baseline": baseline,
            "provider_catalog": catalog,
        },
        "source_files": {"engine": engine},
        "required_tick_days": ["2026-07-06"],
        "tick_contracts": {
            "2026-07-06": {
                "day": "2026-07-06",
                "tick_time_contract": "mt5_utc_v2",
                "time_basis": "UTC",
                "parquet_sha256": "a" * 64,
                "size_bytes": 123,
            }
        },
        "runtime": {
            "python": "3.14.2",
            "packages": {"pandas": "3.0.2", "numpy": "2.4.4", "pyarrow": "23.0.1"},
        },
        "git": {"commit": "1" * 40, "branch": branch, "dirty": False},
    }


def test_run_identity_ignores_mapping_order_paths_git_branch_and_timestamps(tmp_path):
    left = _evidence_fixture(tmp_path / "left", branch="main")
    right = _evidence_fixture(tmp_path / "right", branch="feature/replay")
    right["parameters"] = {"to_date": None, "from_date": "2026-07-06"}

    card_left = provenance.build_run_evidence(**left)
    card_right = provenance.build_run_evidence(**right)

    assert card_left["run_fingerprint"] == card_right["run_fingerprint"]


def test_replay_order_is_part_of_run_identity(tmp_path):
    args = _evidence_fixture(tmp_path)
    first = provenance.build_run_evidence(**args)
    args["selected_payloads"]["replay_trades"].reverse()
    second = provenance.build_run_evidence(**args)
    assert first["run_fingerprint"] != second["run_fingerprint"]


def test_runtime_policy_source_and_tick_changes_change_run_identity(tmp_path):
    roots = [tmp_path / name for name in ("runtime", "policy", "source", "tick")]
    bases = [_evidence_fixture(root) for root in roots]

    runtime_original = provenance.build_run_evidence(**bases[0])["run_fingerprint"]
    bases[0]["runtime"]["packages"]["pandas"] = "3.0.3"
    assert provenance.build_run_evidence(**bases[0])["run_fingerprint"] != runtime_original

    policy_original = provenance.build_run_evidence(**bases[1])["run_fingerprint"]
    bases[1]["policies"][0]["mode"] = "risk_free_allocation"
    assert provenance.build_run_evidence(**bases[1])["run_fingerprint"] != policy_original

    source_original = provenance.build_run_evidence(**bases[2])["run_fingerprint"]
    bases[2]["source_files"]["engine"].write_text("ENGINE_VERSION = 2\n", encoding="utf-8")
    assert provenance.build_run_evidence(**bases[2])["run_fingerprint"] != source_original

    tick_original = provenance.build_run_evidence(**bases[3])["run_fingerprint"]
    bases[3]["tick_contracts"]["2026-07-06"]["parquet_sha256"] = "b" * 64
    assert provenance.build_run_evidence(**bases[3])["run_fingerprint"] != tick_original


def test_result_fingerprint_ignores_only_top_level_run_metadata():
    report_a = _report(generated_at="2026-07-13T10:00:00+00:00")
    report_b = _report(generated_at="2026-07-13T11:00:00+00:00")
    assert provenance.result_fingerprint(report_a) == provenance.result_fingerprint(report_b)
    report_b["policies"][0]["metrics"]["net_pnl"] += 0.01
    assert provenance.result_fingerprint(report_a) != provenance.result_fingerprint(report_b)


def test_card_never_captures_environment_or_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-appear")
    card = provenance.build_run_evidence(**_evidence_fixture(tmp_path))
    assert "must-not-appear" not in json.dumps(card)
    assert "GEMINI_API_KEY" not in json.dumps(card)
```

- [ ] **Step 2: Run the new module tests and verify RED**

Run:

```powershell
python -m pytest tests/test_simulation_run_provenance.py -q
```

Expected: collection FAIL because `simulation_run_provenance` does not exist.

- [ ] **Step 3: Implement canonical JSON and file/runtime diagnostics**

Create the module with strict JSON helpers and no environment capture:

```python
from __future__ import annotations

import copy
import hashlib
import json
import math
import platform
import subprocess
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1
RUNTIME_PACKAGES = ("pandas", "numpy", "pyarrow")


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _portable_path(path: Path, repo_dir: Path) -> str:
    try:
        return path.resolve().relative_to(repo_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def _file_record(role: str, path: Path, repo_dir: Path) -> dict:
    return {
        "role": role,
        "path": _portable_path(path, repo_dir),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def runtime_versions() -> dict:
    packages = {}
    for name in RUNTIME_PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {"python": platform.python_version(), "packages": packages}
```

- [ ] **Step 4: Implement identity and result fingerprints**

Use role-based hashes in the identity, while retaining full file diagnostics in
the card:

```python
def result_fingerprint(report: Mapping[str, Any]) -> str:
    semantic = copy.deepcopy(dict(report))
    semantic.pop("generated_at", None)
    semantic.pop("provenance", None)
    return sha256_json(semantic)


def _payload_records(selected_payloads: Mapping[str, Sequence[Any]]) -> dict:
    return {
        role: {"count": len(values), "sha256": sha256_json(list(values))}
        for role, values in sorted(selected_payloads.items())
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
) -> dict:
    errors = []
    input_records = []
    for role, path in sorted(input_files.items()):
        if not path.is_file():
            errors.append(f"missing_input:{role}")
            continue
        input_records.append(_file_record(role, path, repo_dir))

    source_records = []
    for role, path in sorted(source_files.items()):
        if not path.is_file():
            errors.append(f"missing_source:{role}")
            continue
        source_records.append(_file_record(role, path, repo_dir))

    normalized_ticks = []
    for day in sorted(set(required_tick_days)):
        contract = tick_contracts.get(day)
        if contract is None:
            errors.append(f"unverified_tick_contract:{day}")
        else:
            normalized_ticks.append(_json_safe(dict(contract)))

    selected = _payload_records(selected_payloads)
    policy_record = {"count": len(policies), "sha256": sha256_json(list(policies))}
    current_runtime = _json_safe(dict(runtime or runtime_versions()))
    source_identity = [
        {"role": row["role"], "sha256": row["sha256"]}
        for row in source_records
    ]
    identity = {
        "schema_version": SCHEMA_VERSION,
        "parameters": _json_safe(dict(parameters)),
        "selected_inputs": selected,
        "policies": policy_record,
        "source_files": source_identity,
        "runtime": current_runtime,
        "tick_days": normalized_ticks,
    }
    limitations = (["tick_artifacts_local_cache_only"] if required_tick_days else [])
    reproducibility = {
        "verified_now": not errors,
        "durable": not bool(required_tick_days),
        "errors": errors,
        "limitations": limitations,
        "git": _json_safe(dict(git or git_diagnostics(repo_dir))),
        "runtime": current_runtime,
        "parameters": identity["parameters"],
        "selected_inputs": selected,
        "policies": policy_record,
        "input_artifacts": input_records,
        "source_files": source_records,
        "tick_days": normalized_ticks,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "run_fingerprint": sha256_json(identity),
        "result_fingerprint": result_fingerprint(report),
        "reproducibility": reproducibility,
        "result_summary": result_summary(report),
    }
```

Implement Git and summary diagnostics without reading environment variables:

```python
def _git_command(repo_dir: Path, *args: str) -> tuple[str | None, str | None]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}:{exc}"
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "git_failed").strip()
        return None, error[:500]
    return completed.stdout.strip(), None


def git_diagnostics(repo_dir: Path) -> dict:
    commit, commit_error = _git_command(repo_dir, "rev-parse", "HEAD")
    branch, branch_error = _git_command(repo_dir, "branch", "--show-current")
    status, status_error = _git_command(repo_dir, "status", "--porcelain")
    errors = [error for error in (commit_error, branch_error, status_error) if error]
    return {
        "commit": commit,
        "branch": branch,
        "dirty": bool(status) if status is not None else None,
        "errors": errors,
    }


def result_summary(report: Mapping[str, Any]) -> dict:
    scope = report.get("canonical_scope") or {}
    selection = report.get("selection") or {}
    return {
        "provider_signals": int(scope.get("provider_signals") or 0),
        "executed_trades": int(report.get("executed_trade_count") or 0),
        "policy_count": int(report.get("policy_count") or 0),
        "selected_policy": selection.get("selected_policy"),
        "selection_blockers": list(selection.get("global_blockers") or []),
    }
```

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
python -m pytest tests/test_simulation_run_provenance.py -q
git add simulation_run_provenance.py tests/test_simulation_run_provenance.py
git commit -m "sim: add deterministic run evidence"
```

Expected: all identity tests PASS.

### Task 3: Publish Immutable Content-Addressed Archives

**Files:**
- Modify: `simulation_run_provenance.py`
- Modify: `tests/test_simulation_run_provenance.py`

- [ ] **Step 1: Write failing archive-publication tests**

Add tests for idempotence, corruption, conflicting results, incomplete evidence
and large detail reports:

```python
def _complete_evidence(root: Path):
    args = _evidence_fixture(root)
    report = args["report"]
    return provenance.build_run_evidence(**args), report


def _publish_args(root: Path, evidence: dict, report: dict, *, include_trades=False):
    return {
        "report": report,
        "evidence": evidence,
        "archive_root": root / "runs",
        "output_path": root / "strategy_farm.json",
        "include_trades": include_trades,
        "repo_dir": root,
    }


def test_compact_run_is_published_once_and_idempotent(tmp_path):
    evidence, report = _complete_evidence(tmp_path)
    first = provenance.publish_run_archive(
        report=report,
        evidence=evidence,
        archive_root=tmp_path / "runs",
        output_path=tmp_path / "strategy_farm.json",
        include_trades=False,
        repo_dir=tmp_path,
    )
    second = provenance.publish_run_archive(
        report={**report, "generated_at": "later"},
        evidence={**evidence, "result_fingerprint": provenance.result_fingerprint(report)},
        archive_root=tmp_path / "runs",
        output_path=tmp_path / "strategy_farm.json",
        include_trades=False,
        repo_dir=tmp_path,
    )
    assert first.run_dir == second.run_dir
    assert second.idempotent is True
    assert len(list((tmp_path / "runs").glob("[0-9a-f]*"))) == 1


def test_same_identity_with_different_result_fails_closed(tmp_path):
    evidence, report = _complete_evidence(tmp_path)
    provenance.publish_run_archive(**_publish_args(tmp_path, evidence, report))
    changed = copy.deepcopy(evidence)
    changed["result_fingerprint"] = "f" * 64
    with pytest.raises(provenance.ProvenanceConflictError):
        provenance.publish_run_archive(**_publish_args(tmp_path, changed, report))


def test_corrupt_retained_artifact_fails_closed(tmp_path):
    evidence, report = _complete_evidence(tmp_path)
    first = provenance.publish_run_archive(**_publish_args(tmp_path, evidence, report))
    (first.run_dir / "strategy_farm.json").write_text("corrupt\n", encoding="utf-8")
    with pytest.raises(provenance.ProvenanceConflictError):
        provenance.publish_run_archive(**_publish_args(tmp_path, evidence, report))


def test_incomplete_evidence_marks_latest_report_without_archive(tmp_path):
    evidence, report = _complete_evidence(tmp_path)
    evidence["reproducibility"]["verified_now"] = False
    evidence["reproducibility"]["errors"] = ["unverified_tick_contract:2026-07-06"]
    result = provenance.publish_run_archive(**_publish_args(tmp_path, evidence, report))
    assert result.status == "incomplete"
    assert result.run_dir is None
    assert result.report["provenance"]["status"] == "incomplete"
    assert not (tmp_path / "runs").exists()


def test_detailed_result_is_referenced_but_not_copied(tmp_path):
    args = _evidence_fixture(tmp_path)
    report = args["report"]
    report["includes_trade_details"] = True
    evidence = provenance.build_run_evidence(**args)
    result = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report, include_trades=True),
    )
    card = json.loads((result.run_dir / "run_card.json").read_text())
    assert not (result.run_dir / "strategy_farm.json").exists()
    assert card["artifacts"][0]["retained"] is False
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/test_simulation_run_provenance.py -q
```

Expected: FAIL because publication types/functions do not exist.

- [ ] **Step 3: Implement strict publication and existing-archive validation**

Add focused types and publication flow:

```python
import shutil
import tempfile
from dataclasses import dataclass


class ProvenanceConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicationResult:
    report: dict
    status: str
    run_dir: Path | None
    idempotent: bool


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _provenance_ref(evidence: Mapping[str, Any], status: str, card_path: str | None) -> dict:
    reproducibility = evidence["reproducibility"]
    return {
        "status": status,
        "run_fingerprint": evidence["run_fingerprint"],
        "result_fingerprint": evidence["result_fingerprint"],
        "run_card": card_path,
        "verified_now": reproducibility["verified_now"],
        "durable": reproducibility["durable"],
        "errors": list(reproducibility["errors"]),
        "limitations": list(reproducibility["limitations"]),
    }
```

Implement existing-archive verification and atomic publication:

```python
def _validate_existing(run_dir: Path, evidence: Mapping[str, Any]) -> None:
    card_path = run_dir / "run_card.json"
    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvenanceConflictError(f"invalid_existing_run_card:{type(exc).__name__}") from exc
    for key in ("run_fingerprint", "result_fingerprint"):
        if card.get(key) != evidence.get(key):
            raise ProvenanceConflictError(f"existing_{key}_mismatch")
    for artifact in card.get("artifacts") or []:
        if not artifact.get("retained"):
            continue
        path = run_dir / str(artifact["path"])
        if not path.is_file():
            raise ProvenanceConflictError(f"missing_retained_artifact:{artifact['path']}")
        if path.stat().st_size != int(artifact["size_bytes"]):
            raise ProvenanceConflictError(f"artifact_size_mismatch:{artifact['path']}")
        if sha256_file(path) != artifact["sha256"]:
            raise ProvenanceConflictError(f"artifact_hash_mismatch:{artifact['path']}")


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
    latest = copy.deepcopy(dict(report))
    reproducibility = evidence["reproducibility"]
    if not reproducibility["verified_now"]:
        latest["provenance"] = _provenance_ref(evidence, "incomplete", None)
        return PublicationResult(latest, "incomplete", None, False)

    fingerprint = str(evidence["run_fingerprint"])
    run_dir = Path(archive_root) / fingerprint
    card_path = run_dir / "run_card.json"
    card_ref = _portable_path(card_path, Path(repo_dir))
    latest["provenance"] = _provenance_ref(evidence, "archived", card_ref)
    report_bytes = _pretty_json_bytes(latest)

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

    if run_dir.exists():
        _validate_existing(run_dir, evidence)
        return PublicationResult(latest, "archived", run_dir, True)

    created_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    card = {
        **copy.deepcopy(dict(evidence)),
        "created_at_utc": created_at,
        "artifacts": artifacts,
    }
    archive_root = Path(archive_root)
    archive_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".tmp-run-", dir=archive_root))
    try:
        (temp_dir / "run_card.json").write_bytes(_pretty_json_bytes(card))
        if not include_trades:
            (temp_dir / "strategy_farm.json").write_bytes(report_bytes)
        try:
            temp_dir.replace(run_dir)
        except OSError:
            if not run_dir.exists():
                raise
            _validate_existing(run_dir, evidence)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
    return PublicationResult(latest, "archived", run_dir, False)
```

- [ ] **Step 4: Run tests and commit**

Run:

```powershell
python -m pytest tests/test_simulation_run_provenance.py -q
git add simulation_run_provenance.py tests/test_simulation_run_provenance.py
git commit -m "sim: publish immutable run archives"
```

Expected: publication tests PASS and no temporary directories remain.

### Task 4: Attach Evidence to Strategy Farm CLI Runs

**Files:**
- Modify: `strategy_farm.py`
- Modify: `tests/test_strategy_farm.py`
- Test: `tests/test_simulation_run_provenance.py`

- [ ] **Step 1: Write the failing farm-execution context test**

Test that the report remains unchanged while the companion execution context
contains exactly the inputs used in their computational order:

```python
import json
from pathlib import Path

import pandas as pd


def _farm_trade(sig_id):
    return {
        "sig_id": sig_id,
        "channel": "canal1",
        "direction": "BUY",
        "open_dt_utc": "2026-07-06T10:00:00+00:00",
        "close_dt_utc": "2026-07-06T10:05:00+00:00",
        "tickets": [],
    }


def _provider_signal(sig_id):
    return {
        "provider_signal_id": sig_id,
        "execution_sig_ids": [sig_id],
        "first_observed_utc": "2026-07-06T09:59:59+00:00",
        "signal_ts_utc": "2026-07-06T09:59:58+00:00",
        "semantic_status": "complete",
        "execution_count": 1,
        "channel": "canal1",
        "level_timeline": [],
    }


class _FakeTickLoader:
    def __init__(self, tick_cache_dir):
        self.required_days = ["2026-07-06"]
        self.verified_contracts = {
            "2026-07-06": {
                "day": "2026-07-06",
                "tick_time_contract": "mt5_utc_v2",
                "time_basis": "UTC",
                "parquet_sha256": "a" * 64,
                "size_bytes": 123,
            }
        }

    def load_ticks_for_trade(self, trade, *, pad_minutes=5):
        return pd.DataFrame(), []


def test_farm_execution_exposes_exact_provenance_payloads(tmp_path, monkeypatch):
    policies = [strategy_policies.StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )]
    trades = [_farm_trade("canal1_2"), _farm_trade("canal1_1")]
    baselines = [
        {"sig_id": "canal1_1", "status": "exact"},
        {"sig_id": "canal1_2", "status": "exact"},
    ]
    catalog = {"signals": [_provider_signal("canal1_1"), _provider_signal("canal1_2")]}
    monkeypatch.setattr(
        strategy_farm.observed_tick_replay_validator,
        "ReplayTickFrameCache",
        _FakeTickLoader,
    )
    monkeypatch.setattr(
        strategy_farm.strategy_simulator,
        "simulate_trade",
        lambda *args, **kwargs: _row(0.0, status="unchanged"),
    )

    execution = strategy_farm.build_farm_execution(
        trades,
        baselines,
        tick_cache_dir=tmp_path / "ticks",
        policies=policies,
        catalog=catalog,
        from_date="2026-07-06",
        minimum_trades=1,
    )

    assert execution.report["executed_trade_count"] == 2
    assert [row["sig_id"] for row in execution.selected_payloads["replay_trades"]] == [
        "canal1_2", "canal1_1"
    ]
    assert [row["sig_id"] for row in execution.selected_payloads["effective_baselines"]] == [
        "canal1_2", "canal1_1"
    ]
    assert execution.required_tick_days == ["2026-07-06"]
```

- [ ] **Step 2: Implement `FarmExecution` without changing library callers**

Add the dataclass below. Rename the existing `build_farm_report` function to
`build_farm_execution`, change its return annotation to `FarmExecution`, and
leave its existing simulation statements in their current order. Replace only
its final report dictionary return with the `report` assignment and return
shown below.

```python
from dataclasses import dataclass
import simulation_run_provenance


@dataclass(frozen=True)
class FarmExecution:
    report: dict
    selected_payloads: dict[str, list]
    policies: list[dict]
    required_tick_days: list[str]
    verified_tick_contracts: dict[str, dict]


report = {
    "schema_version": SCHEMA_VERSION,
    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "from_date": from_date,
    "to_date": to_date,
    "executed_trade_count": len(selected_trades),
    "policy_count": len(policies),
    "includes_trade_details": include_trades,
    "calibration": {
        "unit_value": round(unit_value, 8),
        "source": unit_source,
    },
    "canonical_scope": canonical_scope,
    "selection": selection,
    "policies": scores,
}

effective_baselines = [
    {
        "sig_id": str(trade.get("sig_id")),
        "baseline": baselines.get(str(trade.get("sig_id"))),
    }
    for trade in selected_trades
]
effective_providers = [
    {
        "sig_id": str(trade.get("sig_id")),
        "provider_signal": providers.get(str(trade.get("sig_id"))),
    }
    for trade in selected_trades
]
provider_scope = _provider_signals_in_scope(catalog, from_date, to_date)
return FarmExecution(
    report=report,
    selected_payloads={
        "replay_trades": selected_trades,
        "effective_baselines": effective_baselines,
        "effective_provider_links": effective_providers,
        "provider_scope": provider_scope,
    },
    policies=[policy.to_dict() for policy in policies],
    required_tick_days=tick_loader.required_days,
    verified_tick_contracts=tick_loader.verified_contracts,
)
    effective_baselines = [
        {"sig_id": str(trade.get("sig_id")), "baseline": baselines.get(str(trade.get("sig_id")))}
        for trade in selected_trades
    ]
    effective_providers = [
        {"sig_id": str(trade.get("sig_id")), "provider_signal": providers.get(str(trade.get("sig_id")))}
        for trade in selected_trades
    ]
    provider_scope = _provider_signals_in_scope(catalog, from_date, to_date)
    return FarmExecution(
        report=report,
        selected_payloads={
            "replay_trades": selected_trades,
            "effective_baselines": effective_baselines,
            "effective_provider_links": effective_providers,
            "provider_scope": provider_scope,
        },
        policies=[policy.to_dict() for policy in policies],
        required_tick_days=tick_loader.required_days,
        verified_tick_contracts=tick_loader.verified_contracts,
    )


def build_farm_report(
    trades: list[dict],
    baseline_rows: list[dict],
    *,
    tick_cache_dir: Path,
    policies: list[strategy_policies.StrategyPolicy] | None = None,
    catalog: dict | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    minimum_trades: int = 200,
    include_trades: bool = False,
) -> dict:
    return build_farm_execution(
        trades,
        baseline_rows,
        tick_cache_dir=tick_cache_dir,
        policies=policies,
        catalog=catalog,
        from_date=from_date,
        to_date=to_date,
        minimum_trades=minimum_trades,
        include_trades=include_trades,
    ).report
```

Implement `_provider_signals_in_scope()` once and reuse it inside
`_canonical_scope()` so report counts and provenance use the exact same date
rule.

```python
def _provider_signals_in_scope(
    catalog: dict | None,
    from_date: str | None,
    to_date: str | None,
) -> list[dict]:
    selected = []
    for signal in (catalog or {}).get("signals") or []:
        ts = signal.get("first_observed_utc") or signal.get("signal_ts_utc")
        day = str(ts or "")[:10]
        if not day:
            continue
        if from_date and day < from_date:
            continue
        if to_date and day > to_date:
            continue
        selected.append(signal)
    return selected
```

- [ ] **Step 3: Write failing CLI publication tests**

Cover a successful archive, missing required inputs and incomplete tick evidence:

```python
def _write_empty_farm_inputs(root: Path):
    replay = root / "replay.jsonl"
    baseline = root / "baseline.jsonl"
    catalog = root / "catalog.json"
    replay.write_text("", encoding="utf-8")
    baseline.write_text("", encoding="utf-8")
    catalog.write_text('{"schema_version":1,"signals":[]}\n', encoding="utf-8")
    return {"replay": replay, "baseline": baseline, "catalog": catalog}


def test_cli_writes_latest_report_with_run_card_reference(tmp_path):
    paths = _write_empty_farm_inputs(tmp_path)
    exit_code = strategy_farm.main([
        "--replay", str(paths["replay"]),
        "--baseline", str(paths["baseline"]),
        "--catalog", str(paths["catalog"]),
        "--tick-cache-dir", str(tmp_path / "ticks"),
        "--output", str(tmp_path / "strategy_farm.json"),
        "--run-archive-dir", str(tmp_path / "runs"),
        "--quiet",
    ])
    latest = json.loads((tmp_path / "strategy_farm.json").read_text())
    assert exit_code == 0
    assert latest["provenance"]["status"] == "archived"
    assert Path(tmp_path / "runs" / latest["provenance"]["run_fingerprint"] / "run_card.json").is_file()


def test_cli_rejects_missing_catalog_without_reusing_output(tmp_path):
    replay = tmp_path / "replay.jsonl"
    baseline = tmp_path / "baseline.jsonl"
    replay.write_text("", encoding="utf-8")
    baseline.write_text("", encoding="utf-8")
    output = tmp_path / "strategy_farm.json"
    output.write_text('{"generated_at":"stale"}\n')
    exit_code = strategy_farm.main([
        "--replay", str(replay),
        "--baseline", str(baseline),
        "--catalog", str(tmp_path / "missing.json"),
        "--output", str(output),
        "--run-archive-dir", str(tmp_path / "runs"),
        "--quiet",
    ])
    assert exit_code != 0
    assert not output.exists()
```

- [ ] **Step 4: Integrate publication into `main()`**

Add `DEFAULT_RUN_ARCHIVE = DATA_DIR / "simulation_runs"` and the
`--run-archive-dir` argument. Validate the three required input files and remove
the mutable output before doing work. Build one `FarmExecution`, one evidence
object and one publication result:

```python
required_inputs = {
    "replay_trades": args.replay,
    "observed_baseline": args.baseline,
    "provider_catalog": args.catalog,
}
missing = [role for role, path in required_inputs.items() if not path.is_file()]
if missing:
    args.output.unlink(missing_ok=True)
    print(f"Missing strategy-farm inputs: {', '.join(missing)}", file=sys.stderr)
    return 1
args.output.unlink(missing_ok=True)

trades = strategy_simulator.load_jsonl(args.replay)
baseline_rows = strategy_simulator.load_jsonl(args.baseline)
catalog = _load_json(args.catalog)
execution = build_farm_execution(
    trades,
    baseline_rows,
    tick_cache_dir=args.tick_cache_dir,
    catalog=catalog,
    from_date=args.from_date,
    to_date=args.to_date,
    minimum_trades=args.minimum_trades,
    include_trades=args.include_trades,
)
evidence = simulation_run_provenance.build_run_evidence(
    repo_dir=Path(__file__).parent,
    report=execution.report,
    parameters={
        "from_date": args.from_date,
        "to_date": args.to_date,
        "minimum_trades": args.minimum_trades,
        "include_trades": args.include_trades,
        "tick_pad_minutes": 5,
    },
    selected_payloads=execution.selected_payloads,
    policies=execution.policies,
    input_files=required_inputs,
    source_files={
        "strategy_farm": Path(__file__),
        "strategy_policies": Path(strategy_policies.__file__),
        "strategy_simulator": Path(strategy_simulator.__file__),
        "observed_tick_replay_validator": Path(observed_tick_replay_validator.__file__),
        "ensure_replay_tick_cache": Path(observed_tick_replay_validator.ensure_replay_tick_cache.__file__),
        "simulation_run_provenance": Path(simulation_run_provenance.__file__),
    },
    required_tick_days=execution.required_tick_days,
    tick_contracts=execution.verified_tick_contracts,
)
publication = simulation_run_provenance.publish_run_archive(
    report=execution.report,
    evidence=evidence,
    archive_root=args.run_archive_dir,
    output_path=args.output,
    include_trades=args.include_trades,
    repo_dir=Path(__file__).parent,
)
write_report(publication.report, args.output)
```

Catch `ProvenanceConflictError`, remove the mutable output, print the exact
conflict to stderr and return exit code `2`. In non-quiet mode print provenance
status and the run fingerprint in addition to existing lines.

- [ ] **Step 5: Run targeted tests and commit**

Run:

```powershell
python -m pytest tests/test_strategy_farm.py tests/test_simulation_run_provenance.py -q
git add strategy_farm.py tests/test_strategy_farm.py tests/test_simulation_run_provenance.py
git commit -m "sim: attach provenance to strategy farm runs"
```

Expected: existing report/metric tests and new CLI tests PASS.

### Task 5: Synchronize Exact Provider Inputs and Reject Stale Reports

**Files:**
- Modify: `tools/run_bot_watch.py`
- Modify: `run_bot.bat`
- Modify: `tests/test_run_bot_watch.py`
- Modify: `tests/test_run_bot_bat.py`

- [ ] **Step 1: Turn the existing stale-artifact test into a real reproduction**

Change the test to assert failed builders physically remove previous mutable
outputs, and update staging expectations:

```python
def test_failed_offline_builders_remove_stale_mutable_artifacts(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    catalog = data_dir / "provider_signal_catalog.json"
    farm = data_dir / "strategy_farm.json"
    catalog.write_text('{"generated_at":"old"}\n')
    farm.write_text('{"generated_at":"old"}\n')
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "PROVIDER_SIGNAL_CATALOG_FILE", catalog)
    monkeypatch.setattr(watch, "STRATEGY_FARM_FILE", farm)
    monkeypatch.setattr(
        watch.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=1),
    )

    assert watch._regenerate_provider_signal_catalog() is False
    assert watch._regenerate_strategy_farm() is False
    assert not catalog.exists()
    assert not farm.exists()
```

Update `test_push_session_data_adds_reconcile_status`:

```python
assert "data/provider_signal_catalog.json" in added
assert "data/strategy_farm.json" in added
assert "data/simulation_runs" in added
```

Update the batch-file test:

```python
assert r"git add -f data\provider_signal_catalog.json" in text
assert r"data\strategy_farm.json" in text
assert r"data\simulation_runs" in text
```

- [ ] **Step 2: Run watcher tests and verify RED**

Run:

```powershell
python -m pytest tests/test_run_bot_watch.py tests/test_run_bot_bat.py -q
```

Expected: FAIL on stale files and missing staging paths.

- [ ] **Step 3: Clear mutable outputs before builders and stage evidence**

At the start of `_regenerate_provider_signal_catalog()` call
`PROVIDER_SIGNAL_CATALOG_FILE.unlink(missing_ok=True)`. At the start of
`_regenerate_strategy_farm()` call `STRATEGY_FARM_FILE.unlink(missing_ok=True)`.
Do not touch `data/simulation_runs` in either cleanup.

Add these paths to `_push_session_data()`:

```python
files = [
    "data/trade_events.jsonl",
    "data/ledger.jsonl",
    "data/reconcile_status.json",
    "data/replay_trades.jsonl",
    "data/replay_status.json",
    "data/accounting_replay_audit.jsonl",
    "data/accounting_replay_audit_status.json",
    "data/replay_tick_cache_status.json",
    "data/replay_readiness_report.json",
    "data/observed_tick_replay_audit.jsonl",
    "data/observed_tick_replay_status.json",
    "data/provider_signal_catalog.json",
    "data/strategy_farm.json",
    "data/simulation_runs",
    "data/trade_events_TEST.jsonl",
    "data/trade_journal.csv",
    "data/trade_journal_TEST.csv",
]
```

Keep `_git("add", "-f", path)` behavior so a removed tracked stale artifact is
recorded as a deletion rather than silently retained.

Update the final `run_bot.bat` backup:

```bat
git add -f data\provider_signal_catalog.json data\strategy_farm.json 2>nul
if exist data\simulation_runs git add -f data\simulation_runs 2>nul
```

- [ ] **Step 4: Run tests and commit**

Run:

```powershell
python -m pytest tests/test_run_bot_watch.py tests/test_run_bot_bat.py -q
git add tools/run_bot_watch.py run_bot.bat tests/test_run_bot_watch.py tests/test_run_bot_bat.py
git commit -m "fix: preserve exact farm inputs across VM sync"
```

Expected: watcher tests PASS, failed builders leave no mutable stale report, and
immutable archives are untouched.

### Task 6: Document, Generate the Canonical Catalog, and Verify End to End

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Create: `data/provider_signal_catalog.json`
- Verify: all source and tests from Tasks 1-5

- [ ] **Step 1: Document the new pipeline artifact and honest retention status**

Add these points to `AGENTS.md` and `README.md`:

```markdown
- `simulation_run_provenance.py` fingerprints the exact selected farm inputs,
  source files, runtime versions and already-verified tick contracts.
- `data/simulation_runs/<fingerprint>/run_card.json` is immutable evidence;
  repeated identical runs reuse it and conflicting results fail closed.
- `data/provider_signal_catalog.json` is a canonical versioned input, not a
  disposable intermediate.
- Tick digests verify identity, but tick Parquet retention is currently local;
  a run card cannot recreate a deleted cache file.
```

Document `--run-archive-dir` and state that `--include-trades` reports are
referenced rather than copied into the archive.

- [ ] **Step 2: Generate the canonical provider catalog once**

Run:

```powershell
python provider_signal_catalog.py --quiet
```

Expected: `data/provider_signal_catalog.json` exists, its summary matches the
current tracked Telegram/replay inputs, and no credentials are present.

- [ ] **Step 3: Run the complete targeted verification set**

Run:

```powershell
python -m pytest tests/test_ensure_replay_tick_cache.py tests/test_observed_tick_replay_validator.py tests/test_simulation_run_provenance.py tests/test_strategy_farm.py tests/test_run_bot_watch.py tests/test_run_bot_bat.py -q
```

Expected: all targeted tests PASS.

- [ ] **Step 4: Run the complete project suite**

Run:

```powershell
python -m pytest -q
```

Expected: all tests PASS with only the existing optional Gemini skip.

- [ ] **Step 5: Verify repository integrity and data size**

Run:

```powershell
git diff --check
git status -sb
Get-Item data\provider_signal_catalog.json | Select-Object Length,LastWriteTime
git diff --stat origin/main...HEAD
```

Expected: no whitespace errors; only intentional source, tests, docs and the
canonical catalog are changed; no Parquet or credentials are staged.

- [ ] **Step 6: Commit the final documentation and canonical artifact**

Run:

```powershell
git add AGENTS.md README.md data/provider_signal_catalog.json
git commit -m "docs: record simulation provenance workflow"
```

- [ ] **Step 7: Perform final review before integration**

Inspect:

```powershell
git log --oneline --decorate origin/main..HEAD
git diff --check origin/main...HEAD
python -m pytest -q
```

Expected: design and implementation commits are reviewable, the full suite is
green, and no production order path imports `simulation_run_provenance`:

```powershell
rg -n "simulation_run_provenance" main.py listener.py executor.py position_lifecycle_monitor.py
```

Expected final command: no matches.
