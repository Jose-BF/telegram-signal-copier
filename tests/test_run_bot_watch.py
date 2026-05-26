import json
import subprocess

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


def test_push_session_data_adds_reconcile_status(monkeypatch):
    added = []

    monkeypatch.setattr(watch, "_regenerate_ledger", lambda: False)

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
