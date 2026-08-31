import json
import socket
import subprocess
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest

import replay_source_contract
from strategy_shadow_contracts import canonical_hash
import tools.run_bot_watch as watch
from tools import git_sync, runtime_recovery


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


def _recovery_result(**overrides):
    values = {
        "ok": True,
        "action": "clean",
        "source_paths": (),
        "restored_paths": (),
        "archived_paths": (),
        "unsafe_paths": (),
        "commit": None,
        "error": None,
    }
    values.update(overrides)
    return runtime_recovery.RecoveryResult(**values)


def _stub_successful_telemetry(monkeypatch):
    monkeypatch.setattr(
        watch.runtime_telemetry,
        "checkpoint_runtime",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            chunks=(),
            errors=(),
        ),
    )
    monkeypatch.setattr(
        watch.runtime_telemetry,
        "publish_outbox",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            published_files=0,
            commit=None,
            error=None,
        ),
    )


def test_prepare_repository_for_runtime_delegates_to_state_machine(monkeypatch):
    calls = []
    expected = _sync_result(action="attached")

    monkeypatch.setattr(
        watch.runtime_recovery,
        "prepare_runtime_worktree",
        lambda repo_dir, *, runtime_dir: calls.append(
            ("recover", repo_dir, runtime_dir)
        )
        or _recovery_result(),
    )
    monkeypatch.setattr(
        watch.runtime_paths,
        "initialize_runtime_store",
        lambda repo_dir, **kwargs: SimpleNamespace(
            ok=True,
            copied=(),
            preserved=(),
            archived_tails=(),
        ),
    )

    def fake_sync(
        repo_dir,
        *,
        publish_local,
        progress_callback,
        worktree_recovery,
    ):
        calls.append(("sync", repo_dir, publish_local, progress_callback))
        worktree_recovery(repo_dir)
        return expected

    monkeypatch.setattr(watch.git_sync, "synchronize_repository", fake_sync)

    assert watch._prepare_repository_for_runtime() == expected
    assert calls == [
        ("sync", watch.REPO_DIR, False, watch._print_git_progress),
        ("recover", watch.REPO_DIR, watch.RUNTIME_DATA_DIR),
    ]


def test_prepare_repository_passes_unsafe_local_code_to_sync_state_machine(
    monkeypatch,
):
    monkeypatch.setattr(
        watch.runtime_paths,
        "initialize_runtime_store",
        lambda repo_dir, **kwargs: SimpleNamespace(
            ok=True,
            copied=(),
            preserved=(),
            archived_tails=(),
        ),
    )
    monkeypatch.setattr(
        watch.runtime_recovery,
        "prepare_runtime_worktree",
        lambda repo_dir, **kwargs: _recovery_result(
            ok=False,
            action="unsafe_worktree",
            unsafe_paths=("main.py",),
            error="source changes require manual review",
        ),
    )
    def fake_sync(repo_dir, **kwargs):
        recovery = kwargs["worktree_recovery"](repo_dir)
        assert recovery.ok is False
        return _sync_result(
            ok=False,
            action=recovery.action,
            error=recovery.error,
        )

    monkeypatch.setattr(watch.git_sync, "synchronize_repository", fake_sync)

    result = watch._prepare_repository_for_runtime()

    assert result.ok is False
    assert result.action == "unsafe_worktree"
    assert result.error == "source changes require manual review"


def test_fast_checkpoint_is_local_and_never_calls_remote_sync(monkeypatch):
    monkeypatch.setattr(
        watch.runtime_telemetry,
        "checkpoint_runtime",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            chunks=("chunk",),
            pending_tail_bytes={},
            errors=(),
        ),
    )
    monkeypatch.setattr(
        watch.git_sync,
        "synchronize_repository",
        lambda *args, **kwargs: pytest.fail(
            "fast checkpoint must not contact Git remote"
        ),
    )
    monkeypatch.setattr(watch, "_local_head", lambda: "abc123")
    monkeypatch.setattr(watch, "_remote_head", lambda: "def456")
    monkeypatch.setattr(watch, "_current_branch", lambda: "main")

    result = watch._checkpoint_runtime_data()

    assert result.ok is True
    assert result.action == "telemetry_checkpointed"
    assert result.local_head == "abc123"
    assert result.remote_head == "def456"


def test_transport_failure_can_fall_back_to_verified_local_runtime(monkeypatch):
    failed = _sync_result(
        ok=False,
        action="fetch_failed",
        error="github unavailable",
    )
    monkeypatch.setattr(
        watch.git_sync,
        "runtime_head_is_safe",
        lambda repo_dir: True,
    )
    monkeypatch.setattr(watch, "_local_head", lambda: "abc123")
    monkeypatch.setattr(watch, "_remote_head", lambda: "def456")
    monkeypatch.setattr(watch, "_current_branch", lambda: "main")

    fallback = watch._offline_runtime_fallback(failed)

    assert fallback.ok is True
    assert fallback.action == "offline_local_verified"


def test_failed_code_activation_can_relaunch_previous_verified_head(
    monkeypatch,
):
    failed = _sync_result(
        ok=False,
        action="fetch_failed",
        local_head="a" * 40,
        remote_head="b" * 40,
        error="temporary second fetch failure",
    )
    monkeypatch.setattr(
        watch.git_sync,
        "verified_runtime_head_is_available",
        lambda repo_dir, expected_head: expected_head == "a" * 40,
    )
    monkeypatch.setattr(watch, "_local_head", lambda: "a" * 40)
    monkeypatch.setattr(watch, "_remote_head", lambda: "b" * 40)
    monkeypatch.setattr(watch, "_current_branch", lambda: "main")

    fallback = watch._previous_verified_runtime_fallback(
        failed,
        "a" * 40,
    )

    assert fallback.ok is True
    assert fallback.action == "previous_verified_code"
    assert fallback.local_head == "a" * 40
    assert fallback.remote_head == "b" * 40


def test_remote_update_sync_failure_relaunches_previous_bot(monkeypatch):
    class RunningProcess:
        returncode = None

        def poll(self):
            return None

    class InterruptingProcess:
        returncode = None

        def poll(self):
            raise KeyboardInterrupt

    old_process = RunningProcess()
    relaunched_process = InterruptingProcess()
    spawns = [old_process, relaunched_process]
    spawn_heads = []
    verified = _sync_result(local_head="a" * 40, remote_head="a" * 40)
    failed = _sync_result(
        ok=False,
        action="fetch_failed",
        local_head="a" * 40,
        remote_head="b" * 40,
        error="temporary fetch failure",
    )
    preparations = iter((verified, failed))
    fallback_calls = []

    monkeypatch.setattr(
        watch, "_prepare_repository_for_runtime", lambda: next(preparations)
    )
    def spawn(verified_head=None):
        spawn_heads.append(verified_head)
        return spawns.pop(0)

    monkeypatch.setattr(watch, "_spawn_bot_with_active_channels", spawn)
    monkeypatch.setattr(
        watch,
        "_git",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(watch, "_remote_head", lambda: "b" * 40)
    monkeypatch.setattr(watch, "_remote_update_is_data_only", lambda *args: False)
    monkeypatch.setattr(
        watch,
        "_defer_code_update_if_exposed",
        lambda *args, **kwargs: (
            False,
            {"exposure_state": "flat",
             "reason": "heartbeat_reported_flat"},
        ),
    )
    monkeypatch.setattr(
        watch,
        "_quiesce_code_update",
        lambda _process: (
            True,
            {"exposure_state": "flat",
             "reason": "heartbeat_reported_flat"},
        ),
    )
    monkeypatch.setattr(watch, "_clear_runtime_update_pending", lambda: None)
    monkeypatch.setattr(watch, "_paths_changed_between", lambda *args: False)
    monkeypatch.setattr(watch, "_runtime_heartbeat_age_s", lambda **kwargs: 0)
    monkeypatch.setattr(watch, "POLL_SEC", 0)
    monkeypatch.setattr(watch, "TELEMETRY_PUBLISH_SEC", 0)
    monkeypatch.setattr(watch.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        watch,
        "_checkpoint_runtime_data",
        lambda: _sync_result(local_head="a" * 40, remote_head="b" * 40),
    )
    monkeypatch.setattr(watch, "_stop_bot", lambda _process: None)

    def fallback(result, previous_head):
        fallback_calls.append((result, previous_head))
        return _sync_result(
            action="previous_verified_code",
            local_head="a" * 40,
            remote_head="b" * 40,
        )

    monkeypatch.setattr(watch, "_previous_verified_runtime_fallback", fallback)

    assert watch.main() == 0
    assert fallback_calls == [(failed, "a" * 40)]
    assert spawn_heads == [None, "a" * 40]
    assert spawns == []


def test_remote_code_update_does_not_stop_bot_while_exposed(monkeypatch):
    class RunningThenInterrupted:
        returncode = None

        def __init__(self):
            self.poll_calls = 0
            self.stopped = False

        def poll(self):
            self.poll_calls += 1
            if self.poll_calls == 2:
                raise KeyboardInterrupt
            return 0 if self.stopped else None

    process = RunningThenInterrupted()
    stopped = []
    preparations = []
    deferred = []
    verified = _sync_result(
        local_head="a" * 40,
        remote_head="a" * 40,
    )

    def prepare():
        preparations.append(True)
        return verified

    monkeypatch.setattr(watch, "_prepare_repository_for_runtime", prepare)
    monkeypatch.setattr(
        watch, "_spawn_bot_with_active_channels", lambda **kwargs: process)
    monkeypatch.setattr(
        watch,
        "_git",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(watch, "_remote_head", lambda: "b" * 40)
    monkeypatch.setattr(watch, "_remote_update_is_data_only",
                        lambda *args: False)

    def defer(*args, **kwargs):
        deferred.append((args, kwargs))
        return True, {
            "exposure_state": "open",
            "reason": "heartbeat_reported_open",
            "bot_position_count": 5,
        }

    monkeypatch.setattr(watch, "_defer_code_update_if_exposed", defer)
    monkeypatch.setattr(
        watch,
        "_paths_changed_between",
        lambda *args: pytest.fail(
            "code paths must not be inspected after update is deferred"),
    )
    monkeypatch.setattr(watch, "_runtime_heartbeat_age_s",
                        lambda **kwargs: 0)
    monkeypatch.setattr(watch, "POLL_SEC", 0)
    monkeypatch.setattr(watch, "TELEMETRY_PUBLISH_SEC", 0)
    monkeypatch.setattr(watch.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        watch,
        "_checkpoint_runtime_data",
        lambda: _sync_result(
            local_head="a" * 40, remote_head="b" * 40),
    )

    def stop(proc):
        stopped.append(proc)
        proc.stopped = True

    monkeypatch.setattr(watch, "_stop_bot", stop)

    assert watch.main() == 0
    assert len(deferred) == 1
    assert preparations == [True]
    assert stopped == [process]


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


def test_ctrl_c_checkpoints_locally_without_resynchronizing_git(monkeypatch):
    class InterruptedProcess:
        def poll(self):
            raise KeyboardInterrupt

    verified = _sync_result()
    monkeypatch.setattr(watch, "_prepare_repository_for_runtime", lambda: verified)
    monkeypatch.setattr(watch, "_apply_active_channel_manifest", lambda: True)
    monkeypatch.setattr(watch, "_spawn_bot", lambda: InterruptedProcess())
    monkeypatch.setattr(watch, "_stop_bot", lambda _proc: None)
    checkpoints = []
    monkeypatch.setattr(
        watch,
        "_checkpoint_runtime_data",
        lambda: checkpoints.append("local") or _sync_result(
            action="telemetry_checkpointed"
        ),
    )

    assert watch.main() == 0
    assert checkpoints == ["local"]


def test_telemetry_publication_failure_is_nonblocking(monkeypatch):
    monkeypatch.setattr(
        watch.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    assert watch._trigger_telemetry_publication() is False


def test_stale_telemetry_publication_is_terminated_and_replaced(monkeypatch):
    class StaleProcess:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            assert timeout == watch.TELEMETRY_PROCESS_STOP_TIMEOUT_SEC
            return 0

    class FreshProcess:
        def poll(self):
            return None

    stale = StaleProcess()
    fresh = FreshProcess()
    monkeypatch.setattr(watch, "_telemetry_publish_process", stale)
    monkeypatch.setattr(watch, "_telemetry_publish_started_at", 100.0)
    monkeypatch.setattr(watch, "TELEMETRY_PROCESS_MAX_SEC", 30.0)
    monkeypatch.setattr(watch.subprocess, "Popen", lambda *args, **kwargs: fresh)

    assert watch._trigger_telemetry_publication(now=200.0) is True
    assert stale.terminated is True
    assert watch._telemetry_publish_process is fresh
    assert watch._telemetry_publish_started_at == 200.0


def test_unexpected_watcher_failure_stops_child_before_batch_recovery(
    monkeypatch,
):
    class RunningProcess:
        returncode = None

        def poll(self):
            return None

    proc = RunningProcess()
    stopped = []
    monkeypatch.setattr(
        watch,
        "_prepare_repository_for_runtime",
        lambda: _sync_result(),
    )
    monkeypatch.setattr(watch, "_print_sync_result", lambda result: None)
    monkeypatch.setattr(
        watch,
        "_spawn_bot_with_active_channels",
        lambda: proc,
    )
    monkeypatch.setattr(watch, "_stop_bot", lambda child: stopped.append(child))
    monkeypatch.setattr(
        watch,
        "_runtime_heartbeat_age_s",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("watcher failure")),
    )

    with pytest.raises(RuntimeError, match="watcher failure"):
        watch.main()

    assert stopped == [proc]


def test_active_channel_manifest_is_applied_before_bot_spawn(monkeypatch):
    calls = []

    monkeypatch.setattr(
        watch,
        "_apply_active_channel_manifest",
        lambda: calls.append("channels") or True,
    )
    monkeypatch.setattr(
        watch,
        "_spawn_bot",
        lambda: calls.append("spawn") or "process",
    )

    process = watch._spawn_bot_with_active_channels()

    assert process == "process"
    assert calls == ["channels", "spawn"]


def test_active_channel_manifest_failure_blocks_bot_spawn(monkeypatch):
    monkeypatch.setattr(watch, "_apply_active_channel_manifest", lambda: False)
    monkeypatch.setattr(
        watch,
        "_spawn_bot",
        lambda: pytest.fail("bot must not start with stale channel routing"),
    )

    assert watch._spawn_bot_with_active_channels() is None


def test_spawn_bot_attests_the_exact_verified_head(monkeypatch):
    captured = {}
    full_head = "c" * 40
    monkeypatch.setattr(watch, "_local_head", lambda: full_head)
    monkeypatch.setattr(watch, "_remote_head", lambda: full_head)
    monkeypatch.setattr(watch, "_clear_runtime_heartbeat", lambda: None)
    monkeypatch.setattr(
        watch.git_sync,
        "runtime_head_is_safe",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(watch.runtime_control, "clear_for_spawn", lambda: None)

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "process"

    monkeypatch.setattr(watch.subprocess, "Popen", fake_popen)

    assert watch._spawn_bot() == "process"
    assert captured["kwargs"]["env"]["BOT_WATCHER_VERIFIED_HEAD"] == full_head
    assert captured["kwargs"]["env"]["BOT_WATCHER_PID"] == str(watch.os.getpid())
    assert captured["kwargs"]["env"]["BOT_RUNTIME_DATA_DIR"] == str(
        watch.RUNTIME_DATA_DIR
    )


def test_spawn_bot_refuses_head_that_is_not_runtime_safe(monkeypatch):
    monkeypatch.setattr(watch, "_local_head", lambda: "b" * 40)
    monkeypatch.setattr(watch, "_remote_head", lambda: "a" * 40)
    monkeypatch.setattr(
        watch.git_sync,
        "runtime_head_is_safe",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        watch.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("unverified bot must not spawn"),
    )

    assert watch._spawn_bot() is None


def test_spawn_bot_accepts_only_the_explicit_previous_verified_head(
    monkeypatch,
):
    captured = {}
    previous_head = "a" * 40
    monkeypatch.setattr(watch, "_local_head", lambda: previous_head)
    monkeypatch.setattr(watch, "_remote_head", lambda: "b" * 40)
    monkeypatch.setattr(
        watch.git_sync,
        "runtime_head_is_safe",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        watch.git_sync,
        "verified_runtime_head_is_available",
        lambda repo_dir, expected: expected == previous_head,
    )
    monkeypatch.setattr(watch, "_clear_runtime_heartbeat", lambda: None)
    monkeypatch.setattr(watch.runtime_control, "clear_for_spawn", lambda: None)

    def fake_popen(args, **kwargs):
        captured["env"] = kwargs["env"]
        return "process"

    monkeypatch.setattr(watch.subprocess, "Popen", fake_popen)

    assert watch._spawn_bot(verified_head=previous_head) == "process"
    assert captured["env"]["BOT_WATCHER_VERIFIED_HEAD"] == previous_head


def test_watcher_instance_guard_allows_only_one_owner():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    first = watch.WatcherInstanceGuard(port=port)
    second = watch.WatcherInstanceGuard(port=port)

    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        second.release()
        first.release()

    assert second.acquire() is True
    second.release()


def test_runtime_environment_is_scoped_to_watcher_lifetime(monkeypatch):
    monkeypatch.delenv("BOT_RUNTIME_DATA_DIR", raising=False)

    with watch._runtime_environment():
        assert watch.os.environ["BOT_RUNTIME_DATA_DIR"] == str(
            watch.RUNTIME_DATA_DIR
        )

    assert "BOT_RUNTIME_DATA_DIR" not in watch.os.environ


def test_cli_rejects_a_duplicate_watcher_before_any_work(monkeypatch):
    class DuplicateGuard:
        def acquire(self):
            return False

        def release(self):
            pytest.fail("a guard that was not acquired must not be released")

    monkeypatch.setattr(watch, "WatcherInstanceGuard", DuplicateGuard)
    monkeypatch.setattr(
        watch,
        "main",
        lambda: pytest.fail("a duplicate watcher must not run the bot"),
    )
    monkeypatch.setattr(
        watch,
        "_push_session_data",
        lambda: pytest.fail("a duplicate watcher must not publish data"),
    )

    assert watch.cli([]) == watch.WATCHER_DUPLICATE_EXIT_CODE


def test_active_channel_manifest_refuses_to_create_partial_env(
    tmp_path,
    monkeypatch,
):
    manifest = tmp_path / "active_telegram_channels.json"
    manifest.write_text(
        '{"schema_version": 1, "channels": '
        '{"canal2": {"id": -1003908582492}}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(watch, "ACTIVE_CHANNEL_MANIFEST_FILE", manifest)
    monkeypatch.setattr(watch, "ENV_FILE", tmp_path / ".env")

    assert watch._apply_active_channel_manifest() is False
    assert not (tmp_path / ".env").exists()

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
    monkeypatch.setattr(watch, "RUNTIME_DATA_DIR", tmp_path / "data")
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
    monkeypatch.setattr(watch, "RUNTIME_DATA_DIR", data_dir)
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
    ledger = data_dir / "ledger.jsonl"
    events = data_dir / "trade_events.jsonl"
    replay = data_dir / "replay_trades.jsonl"
    ledger.write_text("{}\n", encoding="utf-8")
    events.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "RUNTIME_DATA_DIR", data_dir)
    monkeypatch.setattr(watch, "REPLAY_STATUS_FILE",
                        data_dir / "replay_status.json")

    def fake_run(*args, **kwargs):
        assert args[0] == [watch.sys.executable, "build_replay_trades.py", "--quiet"]
        replay.write_text("{}\n", encoding="utf-8")
        replay_source_contract.write_manifest(
            replay_path=replay,
            ledger_path=ledger,
            events_path=events,
            row_count=1,
        )
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
    assert status["source_contract_verified"] is True
    assert status["source_contract_errors"] == []


def test_regenerate_replay_trades_rejects_missing_source_manifest(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "ledger.jsonl").write_text("{}\n", encoding="utf-8")
    (data_dir / "trade_events.jsonl").write_text("{}\n", encoding="utf-8")
    replay = data_dir / "replay_trades.jsonl"

    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "RUNTIME_DATA_DIR", data_dir)
    monkeypatch.setattr(
        watch,
        "REPLAY_STATUS_FILE",
        data_dir / "replay_status.json",
    )

    def fake_run(*args, **kwargs):
        replay.write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="replay without manifest",
            stderr="",
        )

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    ok = watch._regenerate_replay_trades()

    status = json.loads((data_dir / "replay_status.json").read_text())
    assert ok is False
    assert status["ok"] is False
    assert status["source_contract_verified"] is False
    assert status["source_contract_errors"] == [
        "missing_replay_source_manifest"
    ]


def test_regenerate_accounting_replay_audit_writes_success_status(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    audit = data_dir / "accounting_replay_audit.jsonl"

    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "RUNTIME_DATA_DIR", data_dir)
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
    catalog = data_dir / "provider_signal_catalog.json"
    catalog.write_text('{"signals": []}\n', encoding="utf-8")

    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "REPLAY_TICK_CACHE_STATUS_FILE", tick_status)
    monkeypatch.setattr(watch, "PROVIDER_SIGNAL_CATALOG_FILE", catalog)
    monkeypatch.setattr(
        watch, "_strategy_shadow_until_date", lambda: "2026-08-29",
    )

    def fake_run(*args, **kwargs):
        assert args[0] == [
            watch.sys.executable,
            "tools/ensure_replay_tick_cache.py",
            "--ensure",
            "--since",
            "2026-07-06",
            "--catalog",
            str(catalog),
            "--provider-until",
            "2026-08-29",
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


def test_strategy_shadow_tick_cache_uses_only_its_own_window(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "runtime"
    data_dir.mkdir()
    status = data_dir / "strategy_shadow_tick_cache_status.json"
    catalog = data_dir / "provider_signal_catalog.json"
    catalog.write_text('{"signals": []}\n', encoding="utf-8")

    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "RUNTIME_DATA_DIR", data_dir)
    monkeypatch.setattr(
        watch, "STRATEGY_SHADOW_TICK_CACHE_STATUS_FILE", status,
    )
    monkeypatch.setattr(watch, "PROVIDER_SIGNAL_CATALOG_FILE", catalog)

    def fake_run(*args, **kwargs):
        assert args[0] == [
            watch.sys.executable,
            "tools/ensure_replay_tick_cache.py",
            "--ensure",
            "--input", str(data_dir / "replay_trades.jsonl"),
            "--cache-dir", str(data_dir / "ticks_cache"),
            "--status", str(status),
            "--since", "2026-08-27",
            "--until", "2026-08-29",
            "--catalog", str(catalog),
            "--provider-since", "2026-08-27",
            "--provider-until", "2026-08-29",
            "--quiet",
        ]
        assert kwargs["timeout"] == 900
        status.write_text('{"ok": true}\n', encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="",
        )

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_strategy_shadow_tick_cache_status(
        since_value="2026-08-27",
        until_value="2026-08-29",
    ) is True


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


def test_regenerate_provider_scorecard_validates_derived_output(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    catalog = data_dir / "provider_signal_catalog.json"
    output = data_dir / "provider_result_scorecard.json"
    catalog.write_text('{"signals": []}\n', encoding="utf-8")
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "PROVIDER_SIGNAL_CATALOG_FILE", catalog)
    monkeypatch.setattr(watch, "PROVIDER_RESULT_SCORECARD_FILE", output)

    def fake_run(*args, **kwargs):
        assert args[0] == [
            watch.sys.executable,
            "tools/build_provider_result_scorecard.py",
            "--catalog", str(catalog),
            "--output", str(output),
            "--quiet",
        ]
        output.write_text(json.dumps({
            "schema_version": 1,
            "channel": "canal2",
            "summaries": [],
            "summary": {
                "records": 0,
                "calibration_ready": 0,
                "blocked": 0,
            },
        }), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_provider_result_scorecard() is True


def test_provider_scorecard_validator_rejects_non_object(tmp_path):
    output = tmp_path / "provider_result_scorecard.json"
    output.write_text("[]\n", encoding="utf-8")

    assert watch._provider_result_scorecard_publication_valid(output) is False


def _valid_strategy_farm_publication(root):
    fingerprint = "a" * 64
    card = root / "data" / "simulation_runs" / fingerprint / "run_card.json"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(json.dumps({
        "run_fingerprint": fingerprint,
        "result_fingerprint": "b" * 64,
    }), encoding="utf-8")
    return {
        "primary_universe": "executed_mt5",
        "policy_count": 1,
        "executed_scope": {
            "executed_trades": 1,
            "policy_count": 1,
            "rows_expected": 1,
            "rows_emitted": 1,
            "blocked_rows": 1,
            "entry_invariant_failures": 0,
        },
        "executed_replay_contract": {
            "universe": "executed_mt5",
            "complete": False,
            "rows_expected": 1,
            "rows_emitted": 1,
            "blocked_rows": 1,
            "entry_invariant_failures": 0,
            "blockers": ["blocked_row:canal1_1:no_be"],
        },
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
            "primary_universe": "executed_mt5",
            "price_path_mode": "executed_mt5_entries",
            "executed_contract_complete": False,
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


def _valid_strategy_shadow_publication():
    payload = {
        "schema_version": 1,
        "since": "2026-08-27",
        "until": "2026-08-28",
        "candidate_rows": [],
        "actual_rows": [],
        "tick_evidence": {},
        "report": {
            "comparison_allowed": False,
            "matrix": {
                channel: {
                    "eligible_signals": 0,
                    "expected_rows": 0,
                    "observed_rows": 0,
                    "settled_rows": 0,
                    "blocked_rows": 0,
                    "open_rows": 0,
                    "complete": True,
                }
                for channel in ("canal1", "canal2")
            },
        },
    }
    return {**payload, "settlement_hash": canonical_hash(payload)}


def test_regenerate_strategy_shadow_report_uses_complete_offline_inputs(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "runtime"
    data_dir.mkdir()
    output = data_dir / "strategy_shadow_report.json"
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "RUNTIME_DATA_DIR", data_dir)
    monkeypatch.setattr(watch, "STRATEGY_SHADOW_REPORT_FILE", output)
    monkeypatch.setattr(
        watch,
        "PROVIDER_SIGNAL_CATALOG_FILE",
        data_dir / "provider_signal_catalog.json",
    )
    monkeypatch.setattr(watch, "BROKER_MONEY_CONTRACT_FILE",
                        data_dir / "broker_money_contract.json")
    monkeypatch.setattr(watch, "MONEY_TICK_CACHE_DIR",
                        data_dir / "money_ticks_cache")
    monkeypatch.setattr(watch, "STRATEGY_SHADOW_FROM_DATE", "2026-08-27")
    monkeypatch.setattr(
        watch, "_strategy_shadow_until_date", lambda: "2026-08-28",
    )
    shadow_tick_scopes = []
    monkeypatch.setattr(
        watch,
        "_regenerate_strategy_shadow_tick_cache_status",
        lambda **scope: shadow_tick_scopes.append(scope) or True,
    )

    def fake_run(*args, **kwargs):
        assert args[0] == [
            watch.sys.executable,
            "tools/build_strategy_shadow_report.py",
            "--since", "2026-08-27",
            "--until", "2026-08-28",
            "--events", str(data_dir / "trade_events.jsonl"),
            "--ledger", str(data_dir / "ledger.jsonl"),
            "--ticks-cache", str(data_dir / "ticks_cache"),
            "--money-ticks-cache", str(data_dir / "money_ticks_cache"),
            "--money-contract", str(data_dir / "broker_money_contract.json"),
            "--provider-catalog", str(data_dir / "provider_signal_catalog.json"),
            "--output", str(output),
        ]
        assert kwargs["capture_output"] is False
        output.write_text(
            json.dumps(_valid_strategy_shadow_publication()),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="",
        )

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_strategy_shadow_report() is True
    assert shadow_tick_scopes == [{
        "since_value": "2026-08-27",
        "until_value": "2026-08-28",
    }]


def test_strategy_shadow_automatic_cutoff_uses_last_closed_utc_day():
    assert watch._strategy_shadow_until_date(
        datetime(2026, 8, 30, 0, 1, tzinfo=timezone.utc)
    ) == "2026-08-29"


def test_strategy_shadow_automatic_cutoff_rejects_naive_clock():
    with pytest.raises(ValueError, match="timezone-aware"):
        watch._strategy_shadow_until_date(datetime(2026, 8, 30, 12, 0))


def test_regenerate_strategy_shadow_report_rejects_tampered_output(
        tmp_path, monkeypatch):
    output = tmp_path / "strategy_shadow_report.json"
    monkeypatch.setattr(watch, "STRATEGY_SHADOW_REPORT_FILE", output)

    def fake_run(*args, **kwargs):
        payload = _valid_strategy_shadow_publication()
        payload["report"]["matrix"]["canal1"]["expected_rows"] = 3
        output.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="",
        )

    monkeypatch.setattr(watch.subprocess, "run", fake_run)

    assert watch._regenerate_strategy_shadow_report() is False
    assert not output.exists()


def test_regenerate_strategy_farm_accepts_complete_diagnostic_publication(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report = data_dir / "strategy_farm.json"
    monkeypatch.setattr(watch, "REPO_DIR", tmp_path)
    monkeypatch.setattr(watch, "RUNTIME_DATA_DIR", data_dir)
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


@pytest.mark.parametrize(
    "failure",
    ["row_count", "executed_row_count", "incomplete_provenance"],
)
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
        elif failure == "executed_row_count":
            report["executed_scope"]["rows_emitted"] = 0
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


def test_final_backup_publishes_telemetry_without_mutating_main(monkeypatch):
    monkeypatch.setattr(watch, "_regenerate_session_outputs", lambda: {})
    monkeypatch.setattr(
        watch.runtime_telemetry,
        "checkpoint_runtime",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            chunks=("chunk",),
            errors=(),
        ),
    )
    monkeypatch.setattr(
        watch.runtime_telemetry,
        "publish_outbox",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            published_files=2,
            commit="telemetry-head",
            error=None,
        ),
    )
    monkeypatch.setattr(watch, "_local_head", lambda: "code-head")
    monkeypatch.setattr(watch, "_remote_head", lambda: "code-head")
    monkeypatch.setattr(watch, "_current_branch", lambda: "main")
    monkeypatch.setattr(
        watch,
        "_git",
        lambda *args, **kwargs: pytest.fail(
            f"final backup must not run Git in main: {args}"
        ),
    )

    result = watch._push_session_data()

    assert result.ok is True
    assert result.action == "telemetry_published"
    assert result.local_head == "code-head"


def test_cli_recovery_checkpoint_skips_offline_analysis(monkeypatch):
    monkeypatch.setattr(
        watch,
        "_checkpoint_runtime_data",
        lambda: _sync_result(action="pushed"),
    )
    monkeypatch.setattr(
        watch,
        "_push_session_data",
        lambda: pytest.fail("recovery checkpoint must not run analysis"),
    )

    assert watch.cli(["--recovery-checkpoint"]) == 0


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


def test_interrupted_pipeline_restores_previous_mutable_reports(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    paths = [
        data_dir / "provider_signal_catalog.json",
        data_dir / "provider_result_scorecard.json",
        data_dir / "strategy_farm.json",
        data_dir / "strategy_shadow_report.json",
        data_dir / "log_learning_report.json",
        data_dir / "log_pattern_registry.json",
        data_dir / "log_learning_status.json",
    ]
    for index, path in enumerate(paths):
        path.write_text(f"old-{index}\n", encoding="utf-8")

    monkeypatch.setattr(watch, "PROVIDER_SIGNAL_CATALOG_FILE", paths[0])
    monkeypatch.setattr(watch, "PROVIDER_RESULT_SCORECARD_FILE", paths[1])
    monkeypatch.setattr(watch, "STRATEGY_FARM_FILE", paths[2])
    monkeypatch.setattr(watch, "STRATEGY_SHADOW_REPORT_FILE", paths[3])
    monkeypatch.setattr(watch, "LOG_LEARNING_REPORT_FILE", paths[4])
    monkeypatch.setattr(watch, "LOG_PATTERN_REGISTRY_FILE", paths[5])
    monkeypatch.setattr(watch, "LOG_LEARNING_STATUS_FILE", paths[6])
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

def test_push_pipeline_runs_learning_after_all_causal_builders(monkeypatch):
    _stub_successful_telemetry(monkeypatch)
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
        watch, "_regenerate_strategy_shadow_report", step("shadow"))
    monkeypatch.setattr(
        watch, "_regenerate_replay_readiness_report", step("readiness"))
    monkeypatch.setattr(
        watch, "_regenerate_observed_tick_replay_audit", step("observed"))
    monkeypatch.setattr(
        watch, "_regenerate_provider_signal_catalog", step("provider"))
    monkeypatch.setattr(
        watch, "_regenerate_provider_result_scorecard", step("scorecard"))
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
        "ledger", "replay", "accounting", "provider", "scorecard", "tick_cache",
        "money_contract", "money_ticks", "shadow", "observed",
        "readiness", "farm", "learning",
    ]
    assert all(learning_dependencies[0].values())


def test_shadow_report_ignores_unrelated_global_tick_cache_failure(
        monkeypatch):
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
        watch, "_regenerate_accounting_replay_audit", step("accounting"),
    )
    monkeypatch.setattr(
        watch, "_regenerate_provider_signal_catalog", step("provider"),
    )
    monkeypatch.setattr(
        watch, "_regenerate_provider_result_scorecard", step("scorecard"),
    )
    monkeypatch.setattr(
        watch, "_regenerate_replay_tick_cache_status",
        step("global_ticks", False),
    )
    monkeypatch.setattr(
        watch, "_regenerate_broker_money_contract", step("money_contract"),
    )
    monkeypatch.setattr(
        watch, "_regenerate_money_tick_cache_status", step("money_ticks"),
    )
    monkeypatch.setattr(
        watch, "_regenerate_strategy_shadow_report", step("shadow"),
    )
    monkeypatch.setattr(
        watch, "_regenerate_observed_tick_replay_audit", step("observed"),
    )
    monkeypatch.setattr(
        watch, "_regenerate_replay_readiness_report", step("readiness"),
    )
    monkeypatch.setattr(
        watch, "_regenerate_strategy_farm", step("farm"),
    )
    monkeypatch.setattr(
        watch,
        "_regenerate_recursive_learning_outputs",
        lambda dependencies: calls.append("learning") or True,
    )

    results = watch._regenerate_session_outputs()

    assert results["tick_cache"] is False
    assert results["strategy_shadow"] is True
    assert "shadow" in calls


def test_provider_scorecard_survives_accounting_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(watch, "_clear_mutable_offline_outputs", lambda: None)
    monkeypatch.setattr(watch, "_regenerate_ledger", lambda: True)
    monkeypatch.setattr(watch, "_regenerate_replay_trades", lambda: True)
    monkeypatch.setattr(
        watch, "_regenerate_accounting_replay_audit", lambda: False,
    )
    monkeypatch.setattr(
        watch,
        "_regenerate_provider_signal_catalog",
        lambda: calls.append("provider") or True,
    )
    monkeypatch.setattr(
        watch,
        "_regenerate_provider_result_scorecard",
        lambda: calls.append("scorecard") or True,
    )
    monkeypatch.setattr(
        watch,
        "_regenerate_recursive_learning_outputs",
        lambda dependencies: True,
    )

    results = watch._regenerate_session_outputs()

    assert calls == ["provider", "scorecard"]
    assert results["provider_catalog"] is True
    assert results["provider_scorecard"] is True


def test_session_pipeline_reports_every_stage_in_causal_order(monkeypatch):
    monkeypatch.setattr(watch, "_clear_mutable_offline_outputs", lambda: None)
    stages = [
        ("_regenerate_ledger", "Ledger"),
        ("_regenerate_replay_trades", "Replay"),
        ("_regenerate_accounting_replay_audit", "Auditoria contable"),
        ("_regenerate_provider_signal_catalog", "Catalogo de senales"),
        ("_regenerate_provider_result_scorecard", "Resultados publicados"),
        ("_regenerate_replay_tick_cache_status", "Ticks XAUUSD"),
        ("_regenerate_broker_money_contract", "Contrato monetario"),
        ("_regenerate_money_tick_cache_status", "Ticks de conversion"),
        ("_regenerate_strategy_shadow_report", "Comparativa en sombra"),
        ("_regenerate_observed_tick_replay_audit", "Replay tick a tick"),
        ("_regenerate_replay_readiness_report", "Preparacion de replay"),
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
        (index, 13, f"{label} OK")
        for index, (_, label) in enumerate(
            [*stages, ("learning", "Aprendizaje recursivo")],
            start=1,
        )
    ]


def test_push_pipeline_runs_learning_after_upstream_failure(monkeypatch):
    _stub_successful_telemetry(monkeypatch)
    captured = []
    monkeypatch.setattr(watch, "_clear_mutable_offline_outputs", lambda: None)
    monkeypatch.setattr(watch, "_regenerate_ledger", lambda: False)
    monkeypatch.setattr(
        watch, "_regenerate_provider_signal_catalog", lambda: True,
    )
    monkeypatch.setattr(
        watch, "_regenerate_provider_result_scorecard", lambda: True,
    )
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
        "provider_catalog": True,
        "provider_scorecard": True,
        "readiness": False,
        "replay": False,
        "strategy_farm": False,
        "strategy_shadow": False,
        "tick_cache": False,
        "money_contract": False,
        "money_ticks": False,
    }


def test_push_session_data_clears_stale_farm_when_pipeline_stops_early(
        tmp_path, monkeypatch):
    _stub_successful_telemetry(monkeypatch)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    catalog = data_dir / "provider_signal_catalog.json"
    scorecard = data_dir / "provider_result_scorecard.json"
    farm = data_dir / "strategy_farm.json"
    shadow = data_dir / "strategy_shadow_report.json"
    learning_report = data_dir / "log_learning_report.json"
    pattern_registry = data_dir / "log_pattern_registry.json"
    learning_status = data_dir / "log_learning_status.json"
    catalog.write_text('{"generated_at":"old"}\n', encoding="utf-8")
    scorecard.write_text('{"generated_at":"old"}\n', encoding="utf-8")
    farm.write_text('{"generated_at":"old"}\n', encoding="utf-8")
    shadow.write_text('{"generated_at":"old"}\n', encoding="utf-8")
    learning_report.write_text('{"old":true}\n', encoding="utf-8")
    pattern_registry.write_text('{"old":true}\n', encoding="utf-8")
    learning_status.write_text('{"old":true}\n', encoding="utf-8")
    monkeypatch.setattr(watch, "PROVIDER_SIGNAL_CATALOG_FILE", catalog)
    monkeypatch.setattr(watch, "PROVIDER_RESULT_SCORECARD_FILE", scorecard)
    monkeypatch.setattr(watch, "STRATEGY_FARM_FILE", farm)
    monkeypatch.setattr(watch, "STRATEGY_SHADOW_REPORT_FILE", shadow)
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
    assert not scorecard.exists()
    assert not farm.exists()
    assert not shadow.exists()
    assert not learning_report.exists()
    assert not pattern_registry.exists()
    assert not learning_status.exists()


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


def test_runtime_log_health_change_requires_watcher_self_update():
    assert "tools/runtime_log_health.py" in watch.WATCHER_SELF_UPDATE_PATHS
