import json
import subprocess

import pytest

import tools.run_bot_watch as watch


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
            "--quiet",
        ]
        report.write_text('{"summary": {"blocked": 1}}\n', encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_replay_readiness_report() is True


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
            "--quiet",
        ]
        audit.write_text('{"status": "blocked"}\n', encoding="utf-8")
        status.write_text('{"summary": {"blocked": 1}}\n', encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_observed_tick_replay_audit() is True


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


def test_regenerate_strategy_farm_accepts_report_without_winner(
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
            "--quiet",
        ]
        report.write_text(
            '{"selection": {"selected_policy": null}}\n',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_strategy_farm() is True


def test_regenerate_recursive_learning_accepts_diagnostic_report(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report = data_dir / "log_learning_report.json"
    registry = data_dir / "log_pattern_registry.json"
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "LOG_LEARNING_REPORT_FILE", report)
    monkeypatch.setattr(watch, "LOG_PATTERN_REGISTRY_FILE", registry)

    def fake_run(*args, **kwargs):
        assert args[0] == [
            watch.sys.executable,
            "recursive_log_learning.py",
            "--quiet",
        ]
        report.write_text(
            '{"mode":"diagnostic_only"}\n', encoding="utf-8")
        registry.write_text(
            '{"patterns":[]}\n', encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_recursive_learning_outputs() is True


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
    report.write_text('{"old":true}\n', encoding="utf-8")
    registry.write_text('{"old":true}\n', encoding="utf-8")
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "LOG_LEARNING_REPORT_FILE", report)
    monkeypatch.setattr(watch, "LOG_PATTERN_REGISTRY_FILE", registry)
    monkeypatch.setattr(
        watch.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="failed"),
    )

    assert watch._regenerate_recursive_learning_outputs() is False
    assert not report.exists()
    assert not registry.exists()


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
        watch, "_regenerate_recursive_learning_outputs", lambda: False)

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
        watch, "_regenerate_replay_readiness_report", step("readiness"))
    monkeypatch.setattr(
        watch, "_regenerate_observed_tick_replay_audit", step("observed"))
    monkeypatch.setattr(
        watch, "_regenerate_provider_signal_catalog", step("provider"))
    monkeypatch.setattr(watch, "_regenerate_strategy_farm", step("farm"))
    monkeypatch.setattr(
        watch, "_regenerate_recursive_learning_outputs", step("learning"))

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
        "ledger", "replay", "accounting", "tick_cache", "readiness",
        "observed", "provider", "farm", "learning",
    ]


def test_push_session_data_clears_stale_farm_when_pipeline_stops_early(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    catalog = data_dir / "provider_signal_catalog.json"
    farm = data_dir / "strategy_farm.json"
    learning_report = data_dir / "log_learning_report.json"
    pattern_registry = data_dir / "log_pattern_registry.json"
    catalog.write_text('{"generated_at":"old"}\n', encoding="utf-8")
    farm.write_text('{"generated_at":"old"}\n', encoding="utf-8")
    learning_report.write_text('{"old":true}\n', encoding="utf-8")
    pattern_registry.write_text('{"old":true}\n', encoding="utf-8")
    monkeypatch.setattr(watch, "PROVIDER_SIGNAL_CATALOG_FILE", catalog)
    monkeypatch.setattr(watch, "STRATEGY_FARM_FILE", farm)
    monkeypatch.setattr(watch, "LOG_LEARNING_REPORT_FILE", learning_report)
    monkeypatch.setattr(watch, "LOG_PATTERN_REGISTRY_FILE", pattern_registry)
    monkeypatch.setattr(watch, "_regenerate_ledger", lambda: False)

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


def test_pull_main_ff_uses_explicit_origin_main(monkeypatch):
    calls = []

    def fake_git(*args, capture=True):
        calls.append((args, capture))
        return subprocess.CompletedProcess(args=args, returncode=0,
                                           stdout="", stderr="")

    monkeypatch.setattr(watch, "_git", fake_git)

    watch._pull_main_ff(capture=False)

    assert calls == [(("pull", "--ff-only", "origin", "main"), False)]


def test_pull_main_and_refresh_heads_returns_remote_after_self_data_push(
        monkeypatch):
    calls = []

    def fake_pull(capture=True):
        calls.append(("pull", capture))
        return subprocess.CompletedProcess(args=["git"], returncode=0,
                                           stdout="Already up to date.\n",
                                           stderr="")

    monkeypatch.setattr(watch, "_pull_main_ff", fake_pull)
    monkeypatch.setattr(watch, "_local_head", lambda: "self_data_commit")
    monkeypatch.setattr(watch, "_remote_head", lambda: "self_data_commit")

    pull, local, remote = watch._pull_main_and_refresh_heads()

    assert pull.returncode == 0
    assert local == "self_data_commit"
    assert remote == "self_data_commit"
    assert calls == [("pull", True)]


def test_refresh_after_session_data_push_updates_last_remote(monkeypatch):
    calls = []

    def fake_git(*args, capture=True):
        calls.append((args, capture))
        return subprocess.CompletedProcess(args=args, returncode=0,
                                           stdout="", stderr="")

    monkeypatch.setattr(watch, "_git", fake_git)
    monkeypatch.setattr(watch, "_local_head", lambda: "data_commit")
    monkeypatch.setattr(watch, "_remote_head", lambda: "data_commit")

    local, remote = watch._refresh_heads_after_session_data_push()

    assert local == "data_commit"
    assert remote == "data_commit"
    assert calls == [(("fetch", "origin", "main"), True)]


def test_remote_update_data_only_does_not_require_restart(monkeypatch):
    def fake_git(*args, capture=True):
        assert args == ("log", "--format=%s", "old..new")
        return subprocess.CompletedProcess(
            args=args, returncode=0,
            stdout="data: sesion 2026-05-27 03:10:29\n"
                   "data: sesion 2026-05-27 03:09:29\n",
            stderr="")

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
