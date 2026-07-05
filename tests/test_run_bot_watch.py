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
        assert args[0] == [watch.sys.executable, "replay_builder.py", "--quiet"]
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


def test_regenerate_simulation_audit_writes_success_status(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    audit = data_dir / "simulation_audit.jsonl"

    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "SIMULATION_AUDIT_STATUS_FILE",
                        data_dir / "simulation_audit_status.json")

    def fake_run(*args, **kwargs):
        assert args[0] == [watch.sys.executable, "replay_validator.py", "--quiet"]
        audit.write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="audit ok", stderr="")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    ok = watch._regenerate_simulation_audit()

    assert ok is True
    status = json.loads(
        (data_dir / "simulation_audit_status.json").read_text())
    assert status["ok"] is True
    assert status["returncode"] == 0
    assert status["audit_exists"] is True
    assert status["audit_size_bytes"] == audit.stat().st_size


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

    monkeypatch.setattr(watch, "_regenerate_ledger", lambda: False)
    monkeypatch.setattr(watch, "_regenerate_replay_trades", lambda: False)
    monkeypatch.setattr(watch, "_regenerate_simulation_audit", lambda: False)

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
    assert "data/simulation_audit_status.json" in added
    assert "data/simulation_audit.jsonl" in added


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
