import asyncio
from types import SimpleNamespace

import pytest

import main
import pending_actions
import position_lifecycle_monitor as monitor
from state import Signal
from state import StateManager


class StartupObserved(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_recovery", [False, True])
async def test_startup_defers_management_until_recovery_complete(monkeypatch, fail_recovery):
    calls = []
    spawned = []
    signal = Signal(channel="canal1", message_id=123, direction="BUY")
    queue = pending_actions.PendingQueue()
    monkeypatch.setattr(pending_actions, "queue", queue)
    monkeypatch.setattr(main, "_active_mt5_owner", None)
    monkeypatch.setattr(main, "_git_info", lambda: {})
    monkeypatch.setattr(main, "_watcher_attestation_error", lambda *a, **k: None)
    monkeypatch.setattr(main.journal, "event", lambda *a, **k: None)
    monkeypatch.setattr(main.journal, "set_notify_loop", lambda *a: None)

    async def owner():
        return SimpleNamespace()

    monkeypatch.setattr(main, "_start_mt5_owner", owner)
    monkeypatch.setattr(main.executor, "account_evidence", lambda: {})
    monkeypatch.setattr(main.executor.mt5, "symbol_info", lambda *a: None)
    monkeypatch.setattr(main.executor.mt5, "symbol_info_tick", lambda *a: None)
    monkeypatch.setattr(main.executor, "list_open_positions_grouped", lambda: {})
    monkeypatch.setattr(main, "_assert_dubai_candidate_demo_account", lambda *a: None)
    monkeypatch.setattr(main, "_assert_dubai_candidate_broker_volume", lambda *a: None)
    monkeypatch.setattr(main, "_publish_live_strategy_contract", lambda: None)
    monkeypatch.setattr(main, "_startup_journal_restore_deferred", lambda *a: True)

    async def run_monitor():
        calls.append("monitor")

    def start(*a):
        task = asyncio.create_task(run_monitor())
        spawned.append(task)
        return task

    monkeypatch.setattr(monitor, "start", start)

    def resync(**kwargs):
        staged = kwargs.get("monitor_starts")
        if staged is None:
            start(signal, [])
        else:
            staged.append((signal, []))

    def close():
        signal.requested_close_reason = "PROVIDER_CLOSE"
        pending_actions.enqueue_close_position(signal, 42)
        calls.append("closes")
        return 1

    async def run_queue():
        calls.append("queue")

    async def recover(state):
        await asyncio.sleep(0)
        assert "monitor" not in calls and "queue" not in calls
        assert "closes" in calls
        if fail_recovery:
            raise StartupObserved("recovery failed")
        calls.append("recovered")
        return 1

    def finish():
        raise StartupObserved("startup observed")

    monkeypatch.setattr(queue, "_run", run_queue)
    monkeypatch.setattr(main, "_resync_orphan_positions", resync)
    monkeypatch.setattr(main, "_recover_requested_candidate_closes", close)
    monkeypatch.setattr(monitor, "recover_durable_candidate_entries", recover)
    monkeypatch.setattr(main, "_finalize_journal_orphans", finish)
    try:
        with pytest.raises(StartupObserved):
            await main._run_main()
        await asyncio.sleep(0)
        if fail_recovery:
            assert "queue" not in calls and "monitor" not in calls
            queue._ensure_runner()
            assert queue._task is None
        else:
            assert calls.index("recovered") < calls.index("queue")
            assert calls.index("recovered") < calls.index("monitor")
    finally:
        for task in [*spawned, queue._task]:
            if task is not None:
                task.cancel()
        await asyncio.gather(*spawned, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["{broken", '{"version":2,"actions":[{}]}', '{"version":2,"actions":{}}'])
async def test_startup_never_discards_unreadable_pending_spool(tmp_path, content):
    path = tmp_path / "pending.json"
    path.write_text(content, encoding="utf-8")
    queue = pending_actions.PendingQueue(spool_path=path)
    queue.begin_recovery()
    with pytest.raises(RuntimeError, match="pending spool"):
        queue.restore_from_spool(StateManager())
    assert path.read_text(encoding="utf-8") == content
    assert queue._task is None
