import asyncio
import json
import threading

import pytest

import pending_actions
from pending_actions import PendingAction, PendingQueue
from state import Signal


def action(ticket=42, *, stop=2490.0):
    return PendingAction(
        kind="MODIFY_SLTP", ticket=ticket,
        signal=Signal(channel="canal1", message_id=123, direction="BUY", status="open"),
        new_sl=stop,
    )


@pytest.mark.asyncio
async def test_slow_spool_does_not_block_loop_or_admit_unpersisted_action(tmp_path, monkeypatch):
    queue = PendingQueue(tmp_path / "pending.json")
    monkeypatch.setattr(queue, "_ensure_runner", lambda: None)
    previous, new = action(), action(43)
    queue.add(previous)
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    original = queue._write_spool_payload

    def slow_write(payload):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(3)
        original(payload)

    monkeypatch.setattr(queue, "_write_spool_payload", slow_write)
    task = asyncio.create_task(queue.persist_call(queue.add, new))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        assert not task.done()
        assert queue._action_persisted(previous)
        assert not queue._action_persisted(new)
        assert await queue._try_once(new) == "WAIT_PERSISTENCE"
        release.set()
        await task
        assert queue._action_persisted(new)
        assert len(json.loads(queue._spool_path.read_text())['actions']) == 2
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_spool_preserves_previous_file_and_rejects_new_revision(tmp_path, monkeypatch):
    queue = PendingQueue(tmp_path / "pending.json")
    monkeypatch.setattr(queue, "_ensure_runner", lambda: None)
    old = action()
    queue.add(old)
    before = queue._spool_path.read_bytes()
    original = queue._write_spool_payload

    def failed(payload):
        raise OSError("offline disk full")

    monkeypatch.setattr(queue, "_write_spool_payload", failed)
    with pytest.raises(OSError, match="disk full"):
        await queue.persist_call(queue.add, action(stop=2495.0))
    assert queue._spool_path.read_bytes() == before
    assert not queue._action_persisted(old)
    assert await queue._try_once(old) == "WAIT_PERSISTENCE"
    monkeypatch.setattr(queue, "_write_spool_payload", original)
    await queue.persist_call(queue.add, action(stop=2495.0))
    assert queue._action_persisted(old)
    assert json.loads(queue._spool_path.read_text())['actions'][0]['new_sl'] == 2495.0


@pytest.mark.asyncio
async def test_concurrent_spool_updates_are_serialized_and_cancellation_drains(tmp_path, monkeypatch):
    queue = PendingQueue(tmp_path / "pending.json")
    monkeypatch.setattr(queue, "_ensure_runner", lambda: None)
    entered, release = threading.Event(), threading.Event()
    original = queue._write_spool_payload
    writes = []

    def slow_first(payload):
        if not writes:
            entered.set()
            assert release.wait(3)
        writes.append([row["ticket"] for row in payload["actions"]])
        original(payload)

    monkeypatch.setattr(queue, "_write_spool_payload", slow_first)
    first = asyncio.create_task(queue.persist_call(queue.add, action(42)))
    second = None
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        first.cancel()
        second = asyncio.create_task(queue.persist_call(queue.add, action(43)))
        await asyncio.sleep(.02)
        first.cancel()
        await asyncio.sleep(.02)
        assert not first.done() and not second.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        assert writes == [[42], [42, 43]]
        assert all(queue._action_persisted(row) for row in queue._actions)
        assert [row['ticket'] for row in json.loads(queue._spool_path.read_text())['actions']] == [42, 43]
    finally:
        release.set()
        await asyncio.gather(*(task for task in (first, second) if task), return_exceptions=True)


@pytest.mark.asyncio
async def test_async_enqueue_preserves_effective_stop_return(tmp_path, monkeypatch):
    queue = PendingQueue(tmp_path / "pending.json")
    monkeypatch.setattr(queue, "_ensure_runner", lambda: None)
    monkeypatch.setattr(pending_actions, "queue", queue)
    signal = action().signal
    first = await pending_actions.persist_async(
        pending_actions.enqueue_modify_sl, signal, 42, 2495.0, preserve_stronger=True,
    )
    second = await pending_actions.persist_async(
        pending_actions.enqueue_modify_sl, signal, 42, 2490.0, preserve_stronger=True,
    )
    assert first == second == 2495.0
    assert json.loads(queue._spool_path.read_text())['actions'][0]['new_sl'] == 2495.0


@pytest.mark.asyncio
async def test_gold_first_protection_is_one_persisted_coalesced_action(tmp_path, monkeypatch):
    import listener

    queue = PendingQueue(tmp_path / "pending.json")
    monkeypatch.setattr(queue, "_ensure_runner", lambda: None)
    monkeypatch.setattr(pending_actions, "queue", queue)
    snapshots = []
    original = queue._write_spool_payload

    def write(payload):
        snapshots.append(payload)
        original(payload)

    monkeypatch.setattr(queue, "_write_spool_payload", write)
    stop = await pending_actions.persist_async(
        listener._queue_gold_555_first_leg_protection,
        action().signal, 42, 2490.0, 2510.0,
    )
    assert stop == 2490.0
    assert len(snapshots) == 1
    assert len(snapshots[0]["actions"]) == 1
    assert snapshots[0]["actions"][0]["new_sl"] == 2490.0
    assert snapshots[0]["actions"][0]["new_tp"] == 2510.0


@pytest.mark.asyncio
async def test_runner_keeps_prior_spool_and_reports_failed_completion_checkpoint(tmp_path, monkeypatch):
    from types import SimpleNamespace

    queue = PendingQueue(tmp_path / "pending.json")
    monkeypatch.setattr(queue, "_ensure_runner", lambda: None)
    old = action()
    old.signal.status = "closed"
    queue.add(old)
    before = queue._spool_path.read_bytes()
    original = queue._write_spool_payload
    monkeypatch.setattr(pending_actions.mt5, "symbol_info_tick", lambda *a: SimpleNamespace(time_msc=1))

    def failed(payload):
        raise OSError("offline disk full")

    monkeypatch.setattr(queue, "_write_spool_payload", failed)
    await queue._run()
    assert queue._actions == []
    assert queue._spool_path.read_bytes() == before
    assert queue.persistence_snapshot()["dirty"]
    assert queue.persistence_snapshot()["error"] == "OSError"
    monkeypatch.setattr(queue, "_write_spool_payload", original)
    queue._spool_retry_at = 0
    await queue._persist_spool_if_due()
    assert queue.persistence_snapshot()["error"] is None
    assert json.loads(queue._spool_path.read_text())["actions"] == []
