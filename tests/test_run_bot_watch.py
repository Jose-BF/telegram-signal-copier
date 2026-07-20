import json
import subprocess
from hashlib import sha256

import pytest

import tools.run_bot_watch as watch
from tools import git_sync


def _canonical_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sync_result(**overrides):
    values = {
        "ok": True,
        "action": "up_to_date",
        "branch": "main",
        "local_head": "a" * 40,
        "remote_head": "a" * 40,
    }
    values.update(overrides)
    return git_sync.SyncResult(**values)


def test_prepare_repository_for_runtime_delegates_to_state_machine(monkeypatch):
    calls = []
    expected = _sync_result(action="attached")

    def fake_sync(repo_dir, *, publish_local, progress_callback):
        calls.append((repo_dir, publish_local, progress_callback))
        return expected

    monkeypatch.setattr(watch.git_sync, "synchronize_repository", fake_sync)

    assert watch._prepare_repository_for_runtime() == expected
    assert calls == [(watch.REPO_DIR, True, watch._print_git_progress)]


def test_main_blocks_bot_spawn_when_git_preflight_is_unsafe(monkeypatch):
    blocked = _sync_result(
        ok=False,
        action="rebase_quit_failed",
        branch=None,
        remote_head="b" * 40,
        error="stale rebase could not be closed",
    )
    monkeypatch.setattr(
        watch,
        "_prepare_repository_for_runtime",
        lambda: blocked,
        raising=False,
    )
    monkeypatch.setattr(
        watch,
        "_spawn_bot",
        lambda: pytest.fail("bot must not spawn after unsafe Git preflight"),
    )

    assert watch.main() == watch.WATCHER_GIT_BLOCKED_EXIT_CODE


def test_main_retries_transient_git_transport_failure(monkeypatch):
    retryable = _sync_result(
        ok=False,
        action="fetch_failed",
        error="temporary network failure",
    )
    monkeypatch.setattr(
        watch,
        "_prepare_repository_for_runtime",
        lambda: retryable,
    )
    monkeypatch.setattr(
        watch,
        "_spawn_bot",
        lambda: pytest.fail("bot must not spawn before Git is verified"),
    )

    assert watch.main() == watch.WATCHER_GIT_RETRY_EXIT_CODE


def test_ctrl_c_without_new_commit_still_verifies_git(monkeypatch):
    class InterruptedProcess:
        def poll(self):
            raise KeyboardInterrupt

    verified = _sync_result()
    blocked = _sync_result(
        ok=False,
        action="dirty_worktree",
        error="uncommitted source file",
    )
    refreshes = []
    monkeypatch.setattr(watch, "_prepare_repository_for_runtime", lambda: verified)
    monkeypatch.setattr(watch, "_spawn_bot", lambda: InterruptedProcess())
    monkeypatch.setattr(watch, "_stop_bot", lambda _proc: None)
    monkeypatch.setattr(watch, "_push_session_data", lambda: None)
    monkeypatch.setattr(
        watch,
        "_refresh_heads_after_session_data_push",
        lambda: refreshes.append("sync") or blocked,
    )

    assert watch.main() == watch.WATCHER_GIT_BLOCKED_EXIT_CODE
    assert refreshes == ["sync"]

def _write_learning_artifacts(report_path, registry_path):
    sources = {
        "events": "a" * 64,
        "replay": "b" * 64,
        "accounting": "c" * 64,
        "observed_ticks": "d" * 64,
        "provider_catalog": "e" * 64,
        "strategy_farm": "f" * 64,
        "review_metadata": "1" * 64,
    }
    registry = {
        "schema_version": 1,
        "source_fingerprints": sources,
        "summary": {"patterns": 0},
        "patterns": [],
    }
    registry_bytes = _canonical_bytes(registry)
    report = {
        "schema_version": 1,
        "mode": "diagnostic_only",
        "safe_for_strategy_simulation": False,
        "hard_gate_blockers": ["market_replay"],
        "corpus": {
            "latest_evidence_utc": "2026-07-15T10:00:00+00:00",
            "source_fingerprints": sources,
        },
        "registry_fingerprint": sha256(registry_bytes).hexdigest(),
    }
    report_path.write_bytes(_canonical_bytes(report))
    registry_path.write_bytes(registry_bytes)


def test_regenerate_ledger_writes_failure_status(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "RECONCILE_STATUS_FILE",
                        tmp_path / "data" / "reconcile_status.json")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="partial out",
            stderr="MT5 init failed: terminal unavailable")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    ok = watch._regenerate_ledger()

    assert ok is False
    status = json.loads((tmp_path / "data" / "reconcile_status.json").read_text())
    assert status["ok"] is False
    assert status["returncode"] == 1
    assert status["stdout"] == "partial out"
    assert "MT5 init failed" in status["stderr"]
    assert status["ledger_exists"] is False


def test_regenerate_ledger_writes_success_status(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    ledger = data_dir / "ledger.jsonl"
    ledger.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "RECONCILE_STATUS_FILE",
                        data_dir / "reconcile_status.json")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="ledger ok", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    ok = watch._regenerate_ledger()

    assert ok is True
    status = json.loads((data_dir / "reconcile_status.json").read_text())
    assert status["ok"] is True
    assert status["returncode"] == 0
    assert status["ledger_exists"] is True
    assert status["ledger_size_bytes"] == ledger.stat().st_size


def test_regenerate_replay_trades_writes_success_status(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    replay = data_dir / "replay_trades.jsonl"

    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "REPLAY_STATUS_FILE",
                        data_dir / "replay_status.json")

    def fake_run(*args, **kwargs):
        assert args[0] == [watch.sys.executable, "build_replay_trades.py", "--quiet"]
        replay.write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="replay ok", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    ok = watch._regenerate_replay_trades()

    assert ok is True
    status = json.loads((data_dir / "replay_status.json").read_text())
    assert status["ok"] is True
    assert status["returncode"] == 0
    assert status["replay_exists"] is True
    assert status["replay_size_bytes"] == replay.stat().st_size


def test_regenerate_accounting_replay_audit_writes_success_status(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    audit = data_dir / "accounting_replay_audit.jsonl"

    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "ACCOUNTING_REPLAY_AUDIT_STATUS_FILE",
                        data_dir / "accounting_replay_audit_status.json")

    def fake_run(*args, **kwargs):
        assert args[0] == [watch.sys.executable, "accounting_replay_validator.py", "--quiet"]
        audit.write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="audit ok", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    ok = watch._regenerate_accounting_replay_audit()

    assert ok is True
    status = json.loads(
        (data_dir / "accounting_replay_audit_status.json").read_text())
    assert status["ok"] is True
    assert status["returncode"] == 0
    assert status["audit_exists"] is True
    assert status["audit_size_bytes"] == audit.stat().st_size


def test_regenerate_replay_tick_cache_status_runs_ensure_tool(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    tick_status = data_dir / "replay_tick_cache_status.json"

    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "REPLAY_TICK_CACHE_STATUS_FILE", tick_status)

    def fake_run(*args, **kwargs):
        assert args[0] == [
            watch.sys.executable,
            "tools/ensure_replay_tick_cache.py",
            "--ensure",
            "--since",
            "2026-07-06",
            "--quiet",
        ]
        assert kwargs["timeout"] == 900
        tick_status.write_text('{"ok": true}\n', encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_replay_tick_cache_status() is True


def test_failed_tick_cache_refresh_does_not_accept_stale_status(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    tick_status = data_dir / "replay_tick_cache_status.json"
    tick_status.write_text('{"ok": true, "generated_at": "old"}\n')
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "REPLAY_TICK_CACHE_STATUS_FILE", tick_status)

    def fake_run(*args, **kwargs):
        assert kwargs["timeout"] == 900
        return subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="failed")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_replay_tick_cache_status() is False
    assert not tick_status.exists()


def test_simulation_scope_override_has_precedence(monkeypatch):
    monkeypatch.setattr(watch, "SIMULATION_FROM_DATE", "2026-07-13")
    monkeypatch.setattr(watch, "STRATEGY_FARM_FROM_DATE", "2026-07-06")

    assert watch._simulation_from_date() == "2026-07-13"


def test_regenerate_replay_readiness_report_accepts_blocked_report(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report = data_dir / "replay_readiness_report.json"

    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "REPLAY_READINESS_REPORT_FILE", report)

    def fake_run(*args, **kwargs):
        assert args[0] == [
            watch.sys.executable,
            "replay_readiness_report.py",
            "--since",
            "2026-07-06",
            "--quiet",
        ]
        report.write_text('{"summary": {"blocked": 1}}\n', encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_replay_readiness_report() is True


def test_failed_readiness_generation_removes_stale_report(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report = data_dir / "replay_readiness_report.json"
    report.write_text('{"generated_at": "old"}\n', encoding="utf-8")
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "REPLAY_READINESS_REPORT_FILE", report)
    monkeypatch.setattr(
        watch.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=2, stdout="", stderr="failed"),
    )

    assert watch._regenerate_replay_readiness_report() is False
    assert not report.exists()


def test_regenerate_observed_tick_replay_audit_accepts_blocked_report(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    audit = data_dir / "observed_tick_replay_audit.jsonl"
    status = data_dir / "observed_tick_replay_status.json"

    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "OBSERVED_TICK_REPLAY_AUDIT_FILE", audit)
    monkeypatch.setattr(watch, "OBSERVED_TICK_REPLAY_STATUS_FILE", status)

    def fake_run(*args, **kwargs):
        assert args[0] == [
            watch.sys.executable,
            "observed_tick_replay_validator.py",
            "--since",
            "2026-07-06",
            "--quiet",
        ]
        audit.write_text('{"status": "blocked"}\n', encoding="utf-8")
        status.write_text('{"summary": {"blocked": 1}}\n', encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_observed_tick_replay_audit() is True


def test_failed_observed_generation_removes_stale_artifacts(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    audit = data_dir / "observed_tick_replay_audit.jsonl"
    status = data_dir / "observed_tick_replay_status.json"
    audit.write_text('{"generated_at": "old"}\n', encoding="utf-8")
    status.write_text('{"generated_at": "old"}\n', encoding="utf-8")
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "OBSERVED_TICK_REPLAY_AUDIT_FILE", audit)
    monkeypatch.setattr(watch, "OBSERVED_TICK_REPLAY_STATUS_FILE", status)
    monkeypatch.setattr(
        watch.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=2, stdout="", stderr="failed"),
    )

    assert watch._regenerate_observed_tick_replay_audit() is False
    assert not audit.exists()
    assert not status.exists()


def test_regenerate_provider_signal_catalog_runs_offline_builder(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    catalog = data_dir / "provider_signal_catalog.json"
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "PROVIDER_SIGNAL_CATALOG_FILE", catalog)

    def fake_run(*args, **kwargs):
        assert args[0] == [
            watch.sys.executable,
            "provider_signal_catalog.py",
            "--quiet",
        ]
        catalog.write_text('{"summary": {"provider_signals": 1}}\n')
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_provider_signal_catalog() is True


def _valid_strategy_farm_publication(root):
    fingerprint = "a" * 64
    card = root / "data" / "simulation_runs" / fingerprint / "run_card.json"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(json.dumps({
        "run_fingerprint": fingerprint,
        "result_fingerprint": "b" * 64,
    }), encoding="utf-8")
    return {
        "policy_count": 1,
        "provider_scope": {
            "formal_signals": 1,
            "policy_count": 1,
            "latency_scenarios_ms": [0],
            "rows_expected": 1,
            "rows_emitted": 1,
            "simulated_rows": 0,
            "blocked_rows": 1,
            "signals_omitted": [],
        },
        "selection": {"selected_policy": None},
        "validation": {
            "price_path_mode": "provider_first",
            "money_mode": "diagnostic_only",
            "mode": "diagnostic_only",
        },
        "provenance": {
            "status": "diagnostic_archived",
            "run_fingerprint": fingerprint,
            "result_fingerprint": "b" * 64,
            "run_card": (
                f"data/simulation_runs/{fingerprint}/run_card.json"
            ),
        },
    }


def test_regenerate_strategy_farm_accepts_complete_diagnostic_publication(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report = data_dir / "strategy_farm.json"
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "STRATEGY_FARM_FILE", report)
    monkeypatch.setattr(watch, "STRATEGY_FARM_FROM_DATE", "2026-07-06")

    def fake_run(*args, **kwargs):
        assert args[0] == [
            watch.sys.executable,
            "strategy_farm.py",
            "--from",
            "2026-07-06",
            "--provider-latency-ms",
            "0",
            "--provider-volume-per-leg",
            "0.01",
            "--quiet",
            "--progress",
            "--money-contract",
            str(tmp_path / "data" / "broker_money_contract.json"),
            "--money-tick-cache-dir",
            str(tmp_path / "data" / "money_ticks_cache"),
        ]
        assert kwargs["capture_output"] is False
        report.write_text(
            json.dumps(_valid_strategy_farm_publication(tmp_path)),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_strategy_farm() is True


@pytest.mark.parametrize("failure", ["row_count", "incomplete_provenance"])
def test_regenerate_strategy_farm_rejects_unpublishable_output(
        tmp_path, monkeypatch, failure):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report_path = data_dir / "strategy_farm.json"
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "STRATEGY_FARM_FILE", report_path)
    monkeypatch.setattr(watch, "STRATEGY_FARM_FROM_DATE", "2026-07-06")

    def fake_run(*args, **kwargs):
        report = _valid_strategy_farm_publication(tmp_path)
        if failure == "row_count":
            report["provider_scope"]["rows_emitted"] = 0
        else:
            report["provenance"] = {
                "status": "incomplete",
                "run_card": None,
            }
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_strategy_farm() is False
    assert not report_path.exists()


def test_regenerate_recursive_learning_accepts_diagnostic_report(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report = data_dir / "log_learning_report.json"
    registry = data_dir / "log_pattern_registry.json"
    status = data_dir / "log_learning_status.json"
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "LOG_LEARNING_REPORT_FILE", report)
    monkeypatch.setattr(watch, "LOG_PATTERN_REGISTRY_FILE", registry)
    monkeypatch.setattr(watch, "LOG_LEARNING_STATUS_FILE", status)
    monkeypatch.setattr(
        watch.learning_publication,
        "_read_repository_state",
        lambda root: {
            "git_commit": "9" * 40,
            "git_dirty": False,
            "source_dirty": False,
        },
    )
    monkeypatch.setattr(
        watch.learning_publication,
        "_expected_learning_bytes",
        lambda root: (report.read_bytes(), registry.read_bytes()),
    )
    monkeypatch.setattr(
        watch,
        "_git",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr="",
        ),
    )

    def fake_run(*args, **kwargs):
        assert args[0] == [
            watch.sys.executable,
            "recursive_log_learning.py",
            "--quiet",
        ]
        _write_learning_artifacts(report, registry)
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    dependencies = {
        "accounting": True,
        "observed_ticks": True,
        "provider_catalog": True,
        "strategy_farm": True,
    }
    assert watch._regenerate_recursive_learning_outputs(dependencies) is True
    published = json.loads(status.read_text(encoding="utf-8"))
    assert published["ok"] is True
    assert published["fresh"] is True
    assert published["conclusions_allowed"] is False


def test_failed_offline_builders_remove_stale_mutable_artifacts(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    catalog = data_dir / "provider_signal_catalog.json"
    farm = data_dir / "strategy_farm.json"
    catalog.write_text('{"generated_at": "old"}\n', encoding="utf-8")
    farm.write_text('{"generated_at": "old"}\n', encoding="utf-8")
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "PROVIDER_SIGNAL_CATALOG_FILE", catalog)
    monkeypatch.setattr(watch, "STRATEGY_FARM_FILE", farm)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="failed")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_provider_signal_catalog() is False
    assert watch._regenerate_strategy_farm() is False
    assert not catalog.exists()
    assert not farm.exists()


def test_failed_recursive_learning_removes_both_stale_outputs(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report = data_dir / "log_learning_report.json"
    registry = data_dir / "log_pattern_registry.json"
    status = data_dir / "log_learning_status.json"
    report.write_text('{"old":true}\n', encoding="utf-8")
    registry.write_text('{"old":true}\n', encoding="utf-8")
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "LOG_LEARNING_REPORT_FILE", report)
    monkeypatch.setattr(watch, "LOG_PATTERN_REGISTRY_FILE", registry)
    monkeypatch.setattr(watch, "LOG_LEARNING_STATUS_FILE", status)
    monkeypatch.setattr(
        watch.learning_publication,
        "_read_repository_state",
        lambda root: {
            "git_commit": "9" * 40,
            "git_dirty": False,
            "source_dirty": False,
        },
    )
    monkeypatch.setattr(
        watch,
        "_git",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr="",
        ),
    )
    monkeypatch.setattr(
        watch.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="failed"),
    )

    assert watch._regenerate_recursive_learning_outputs({
        "provider_catalog": True,
    }) is False
    assert not report.exists()
    assert not registry.exists()
    published = json.loads(status.read_text(encoding="utf-8"))
    assert published["ok"] is False
    assert "learning_build_failed:1" in published["blockers"]


def test_regenerate_ledger_records_keyboard_interrupt_before_reraising(
        tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "RECONCILE_STATUS_FILE",
                        tmp_path / "data" / "reconcile_status.json")

    def fake_run(*args, **kwargs):
        raise KeyboardInterrupt("ctrl-break")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    with pytest.raises(KeyboardInterrupt):
        watch._regenerate_ledger()

    status = json.loads((tmp_path / "data" / "reconcile_status.json").read_text())
    assert status["ok"] is False
    assert status["returncode"] is None
    assert status["exception_type"] == "KeyboardInterrupt"
    assert "ctrl-break" in status["stderr"]


def test_cli_final_backup_returns_verified_status(monkeypatch):
    monkeypatch.setattr(
        watch,
        "_push_session_data",
        lambda: _sync_result(action="pushed"),
    )

    assert watch.cli(["--final-backup"]) == 0


def test_cli_final_backup_returns_retry_status_for_transport_failure(monkeypatch):
    monkeypatch.setattr(
        watch,
        "_push_session_data",
        lambda: _sync_result(
            ok=False,
            action="push_failed",
            error="remote unavailable",
        ),
    )

    assert watch.cli(["--final-backup"]) == watch.WATCHER_GIT_RETRY_EXIT_CODE


def test_cli_final_backup_verifies_repository_when_nothing_was_committed(
        monkeypatch):
    calls = []
    blocked = _sync_result(
        ok=False,
        action="dirty_worktree",
        error="uncommitted source file",
    )
    monkeypatch.setattr(watch, "_push_session_data", lambda: None)
    monkeypatch.setattr(
        watch,
        "_prepare_repository_for_runtime",
        lambda: calls.append("sync") or blocked,
    )

    assert watch.cli(["--final-backup"]) == watch.WATCHER_GIT_BLOCKED_EXIT_CODE
    assert calls == ["sync"]


def test_interrupted_pipeline_restores_previous_mutable_reports(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    paths = [
        data_dir / "provider_signal_catalog.json",
        data_dir / "strategy_farm.json",
        data_dir / "log_learning_report.json",
        data_dir / "log_pattern_registry.json",
        data_dir / "log_learning_status.json",
    ]
    for index, path in enumerate(paths):
        path.write_text(f"old-{index}\n", encoding="utf-8")

    monkeypatch.setattr(watch, "PROVIDER_SIGNAL_CATALOG_FILE", paths[0])
    monkeypatch.setattr(watch, "STRATEGY_FARM_FILE", paths[1])
    monkeypatch.setattr(watch, "LOG_LEARNING_REPORT_FILE", paths[2])
    monkeypatch.setattr(watch, "LOG_PATTERN_REGISTRY_FILE", paths[3])
    monkeypatch.setattr(watch, "LOG_LEARNING_STATUS_FILE", paths[4])
    monkeypatch.setattr(
        watch,
        "_regenerate_ledger",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt("stop")),
    )

    with pytest.raises(KeyboardInterrupt):
        watch._push_session_data()

    assert [path.read_text(encoding="utf-8") for path in paths] == [
        f"old-{index}\n" for index in range(len(paths))
    ]

def test_push_session_data_uses_verified_sync_instead_of_legacy_pull(monkeypatch):
    calls = []
    expected = _sync_result(action="pushed")
    monkeypatch.setattr(watch, "_clear_mutable_offline_outputs", lambda: None)
    monkeypatch.setattr(watch, "_regenerate_ledger", lambda: False)
    monkeypatch.setattr(
        watch,
        "_regenerate_recursive_learning_outputs",
        lambda dependencies: False,
    )
    monkeypatch.setattr(
        watch,
        "_prepare_repository_for_runtime",
        lambda: calls.append(("sync",)) or expected,
    )

    def fake_git(*args, capture=True):
        calls.append(args)
        returncode = 1 if args == ("diff", "--cached", "--quiet") else 0
        return subprocess.CompletedProcess(
            args=args,
            returncode=returncode,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(watch, "_git", fake_git)

    result = watch._push_session_data()

    assert result == expected
    assert calls.count(("sync",)) == 1
    assert not any(call[:1] == ("pull",) for call in calls)
    assert ("push", "origin", "main") not in calls

def test_push_session_data_adds_reconcile_status(monkeypatch):
    added = []

    monkeypatch.setattr(
        watch,
        "_clear_mutable_offline_outputs",
        lambda: None,
    )
    monkeypatch.setattr(watch, "_regenerate_ledger", lambda: False)
    monkeypatch.setattr(watch, "_regenerate_replay_trades", lambda: False)
    monkeypatch.setattr(watch, "_regenerate_accounting_replay_audit", lambda: False)
    monkeypatch.setattr(watch, "_regenerate_replay_tick_cache_status", lambda: False)
    monkeypatch.setattr(watch, "_regenerate_replay_readiness_report", lambda: False)
    monkeypatch.setattr(watch, "_regenerate_observed_tick_replay_audit", lambda: False)
    monkeypatch.setattr(watch, "_regenerate_provider_signal_catalog", lambda: False)
    monkeypatch.setattr(watch, "_regenerate_strategy_farm", lambda: False)
    monkeypatch.setattr(
        watch, "_regenerate_recursive_learning_outputs",
        lambda dependencies: False,
    )

    def fake_git(*args, capture=True):
        if args[:2] == ("add", "-f"):
            added.append(args[2])
            return subprocess.CompletedProcess(args=args, returncode=0,
                                               stdout="", stderr="")
        if args == ("diff", "--cached", "--quiet"):
            return subprocess.CompletedProcess(args=args, returncode=0,
                                               stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0,
                                           stdout="", stderr="")

    monkeypatch.setattr(watch, "_git", fake_git)

    watch._push_session_data()

    assert "data/reconcile_status.json" in added
    assert "data/replay_status.json" in added
    assert "data/replay_trades.jsonl" in added
    assert "data/accounting_replay_audit_status.json" in added
    assert "data/accounting_replay_audit.jsonl" in added
    assert "data/replay_tick_cache_status.json" in added
    assert "data/replay_readiness_report.json" in added
    assert "data/observed_tick_replay_audit.jsonl" in added
    assert "data/observed_tick_replay_status.json" in added
    assert "data/provider_signal_catalog.json" in added
    assert "data/strategy_farm.json" in added
    assert "data/log_learning_report.json" in added
    assert "data/log_pattern_registry.json" in added
    assert "data/log_learning_status.json" in added
    assert "data/log_pattern_reviews.json" in added
    assert "data/simulation_runs" in added


def test_push_pipeline_runs_learning_after_all_causal_builders(monkeypatch):
    calls = []
    monkeypatch.setattr(watch, "_clear_mutable_offline_outputs", lambda: None)

    def step(name, result=True):
        def run():
            calls.append(name)
            return result
        return run

    monkeypatch.setattr(watch, "_regenerate_ledger", step("ledger"))
    monkeypatch.setattr(watch, "_regenerate_replay_trades", step("replay"))
    monkeypatch.setattr(
        watch, "_regenerate_accounting_replay_audit", step("accounting"))
    monkeypatch.setattr(
        watch, "_regenerate_replay_tick_cache_status", step("tick_cache"))
    monkeypatch.setattr(
        watch, "_regenerate_broker_money_contract", step("money_contract"))
    monkeypatch.setattr(
        watch, "_regenerate_money_tick_cache_status", step("money_ticks"))
    monkeypatch.setattr(
        watch, "_regenerate_replay_readiness_report", step("readiness"))
    monkeypatch.setattr(
        watch, "_regenerate_observed_tick_replay_audit", step("observed"))
    monkeypatch.setattr(
        watch, "_regenerate_provider_signal_catalog", step("provider"))
    monkeypatch.setattr(watch, "_regenerate_strategy_farm", step("farm"))
    learning_dependencies = []

    def learning(dependencies):
        calls.append("learning")
        learning_dependencies.append(dict(dependencies))
        return True

    monkeypatch.setattr(
        watch, "_regenerate_recursive_learning_outputs", learning)

    def fake_git(*args, capture=True):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(watch, "_git", fake_git)

    watch._push_session_data()

    assert calls == [
        "ledger", "replay", "accounting", "tick_cache", "money_contract",
        "money_ticks", "observed",
        "readiness", "provider", "farm", "learning",
    ]
    assert all(learning_dependencies[0].values())


def test_session_pipeline_reports_every_stage_in_causal_order(monkeypatch):
    monkeypatch.setattr(watch, "_clear_mutable_offline_outputs", lambda: None)
    stages = [
        ("_regenerate_ledger", "Ledger"),
        ("_regenerate_replay_trades", "Replay"),
        ("_regenerate_accounting_replay_audit", "Auditoria contable"),
        ("_regenerate_replay_tick_cache_status", "Ticks XAUUSD"),
        ("_regenerate_broker_money_contract", "Contrato monetario"),
        ("_regenerate_money_tick_cache_status", "Ticks de conversion"),
        ("_regenerate_observed_tick_replay_audit", "Replay tick a tick"),
        ("_regenerate_replay_readiness_report", "Preparacion de replay"),
        ("_regenerate_provider_signal_catalog", "Catalogo de senales"),
        ("_regenerate_strategy_farm", "Granja de estrategias"),
    ]
    for function_name, _ in stages:
        monkeypatch.setattr(watch, function_name, lambda: True)
    monkeypatch.setattr(
        watch,
        "_regenerate_recursive_learning_outputs",
        lambda dependencies: True,
    )

    class Recorder:
        def __init__(self):
            self.updates = []

        def update(self, current, total, label, *, force=False):
            self.updates.append((current, total, label, force))

    reporter = Recorder()
    watch._regenerate_session_outputs(progress_reporter=reporter)

    completed = [
        (current, total, label)
        for current, total, label, _ in reporter.updates
        if label.endswith(" OK")
    ]
    assert completed == [
        (index, 11, f"{label} OK")
        for index, (_, label) in enumerate(
            [*stages, ("learning", "Aprendizaje recursivo")],
            start=1,
        )
    ]


def test_push_session_data_reports_exact_staged_worktree_bytes(
    tmp_path,
    monkeypatch,
    capsys,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "one.json").write_bytes(b"abc")
    (data_dir / "two.jsonl").write_bytes(b"12345")
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "_regenerate_session_outputs", lambda: {})

    def fake_git(*args, capture=True):
        if args == ("diff", "--cached", "--quiet"):
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr=""
            )
        if args == ("diff", "--cached", "--name-only", "-z"):
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="data/one.json\0data/two.jsonl\0",
                stderr="",
            )
        if args[:2] == ("commit", "-m"):
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="stop after size"
            )
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(watch, "_git", fake_git)

    watch._push_session_data()

    output = capsys.readouterr().out
    assert "2 archivos" in output
    assert "8 B" in output


def test_push_pipeline_runs_learning_after_upstream_failure(monkeypatch):
    captured = []
    monkeypatch.setattr(watch, "_clear_mutable_offline_outputs", lambda: None)
    monkeypatch.setattr(watch, "_regenerate_ledger", lambda: False)
    monkeypatch.setattr(
        watch,
        "_regenerate_recursive_learning_outputs",
        lambda dependencies: captured.append(dict(dependencies)) or False,
    )
    monkeypatch.setattr(
        watch,
        "_git",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr="",
        ),
    )

    watch._push_session_data()

    assert len(captured) == 1
    assert captured[0] == {
        "accounting": False,
        "ledger": False,
        "observed_ticks": False,
        "provider_catalog": False,
        "readiness": False,
        "replay": False,
        "strategy_farm": False,
        "tick_cache": False,
        "money_contract": False,
        "money_ticks": False,
    }


def test_push_session_data_clears_stale_farm_when_pipeline_stops_early(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    catalog = data_dir / "provider_signal_catalog.json"
    farm = data_dir / "strategy_farm.json"
    learning_report = data_dir / "log_learning_report.json"
    pattern_registry = data_dir / "log_pattern_registry.json"
    learning_status = data_dir / "log_learning_status.json"
    catalog.write_text('{"generated_at":"old"}\n', encoding="utf-8")
    farm.write_text('{"generated_at":"old"}\n', encoding="utf-8")
    learning_report.write_text('{"old":true}\n', encoding="utf-8")
    pattern_registry.write_text('{"old":true}\n', encoding="utf-8")
    learning_status.write_text('{"old":true}\n', encoding="utf-8")
    monkeypatch.setattr(watch, "PROVIDER_SIGNAL_CATALOG_FILE", catalog)
    monkeypatch.setattr(watch, "STRATEGY_FARM_FILE", farm)
    monkeypatch.setattr(watch, "LOG_LEARNING_REPORT_FILE", learning_report)
    monkeypatch.setattr(watch, "LOG_PATTERN_REGISTRY_FILE", pattern_registry)
    monkeypatch.setattr(watch, "LOG_LEARNING_STATUS_FILE", learning_status)
    monkeypatch.setattr(watch, "_regenerate_ledger", lambda: False)
    monkeypatch.setattr(
        watch,
        "_regenerate_recursive_learning_outputs",
        lambda dependencies: False,
    )

    def fake_git(*args, capture=True):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(watch, "_git", fake_git)

    watch._push_session_data()

    assert not catalog.exists()
    assert not farm.exists()
    assert not learning_report.exists()
    assert not pattern_registry.exists()
    assert not learning_status.exists()


def test_refresh_after_session_data_push_uses_verified_state(monkeypatch):
    expected = _sync_result(action="fast_forwarded")
    calls = []
    monkeypatch.setattr(
        watch,
        "_prepare_repository_for_runtime",
        lambda: calls.append("sync") or expected,
    )

    result = watch._refresh_heads_after_session_data_push()

    assert result == expected
    assert calls == ["sync"]

def test_remote_update_data_only_does_not_require_restart(monkeypatch):
    def fake_git(*args, capture=True):
        if args == ("log", "--format=%s", "old..new"):
            stdout = (
                "data: sesion 2026-05-27 03:10:29\n"
                "data: sesion 2026-05-27 03:09:29\n"
            )
        elif args == (
            "diff", "--name-only", "--no-renames", "old..new"
        ):
            stdout = "data/trade_events.jsonl\ndata/ledger.jsonl\n"
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout=stdout, stderr=""
        )

    monkeypatch.setattr(watch, "_git", fake_git)

    assert watch._remote_update_is_data_only("old", "new") is True


def test_remote_update_with_code_commit_requires_restart(monkeypatch):
    def fake_git(*args, capture=True):
        assert args == ("log", "--format=%s", "old..new")
        return subprocess.CompletedProcess(
            args=args, returncode=0,
            stdout="data: sesion 2026-05-27 03:10:29\n"
                   "fix: track ambiguous market fills\n",
            stderr="")

    monkeypatch.setattr(watch, "_git", fake_git)

    assert watch._remote_update_is_data_only("old", "new") is False


def test_remote_data_subject_that_changes_code_requires_restart(monkeypatch):
    def fake_git(*args, capture=True):
        if args == ("log", "--format=%s", "old..new"):
            stdout = "data: misleading subject\n"
        elif args == (
            "diff", "--name-only", "--no-renames", "old..new"
        ):
            stdout = "main.py\n"
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout=stdout, stderr=""
        )

    monkeypatch.setattr(watch, "_git", fake_git)

    assert watch._remote_update_is_data_only("old", "new") is False


def test_paths_changed_between_detects_watcher_update(monkeypatch):
    def fake_git(*args, capture=True):
        assert args == ("diff", "--name-only", "old..new")
        return subprocess.CompletedProcess(
            args=args, returncode=0,
            stdout="listener.py\n"
                   "tools/run_bot_watch.py\n",
            stderr="")

    monkeypatch.setattr(watch, "_git", fake_git)

    assert watch._paths_changed_between(
        "old", "new", {"tools/run_bot_watch.py"}) is True


def test_paths_changed_between_ignores_unrelated_files(monkeypatch):
    def fake_git(*args, capture=True):
        assert args == ("diff", "--name-only", "old..new")
        return subprocess.CompletedProcess(
            args=args, returncode=0,
            stdout="listener.py\n"
                   "executor.py\n",
            stderr="")

    monkeypatch.setattr(watch, "_git", fake_git)

    assert watch._paths_changed_between(
        "old", "new", {"tools/run_bot_watch.py"}) is False
