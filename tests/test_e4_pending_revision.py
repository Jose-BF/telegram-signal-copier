import asyncio
from types import SimpleNamespace

import pytest

from durable_execution import DurableExecutionResult, ExecutionDisposition
from execution_intents import IntentRecord
from mt5_protocol import IntentState
from pending_actions import PendingQueue
from tests.test_pending_actions import _DurableServiceDouble, _make_action


@pytest.mark.asyncio
@pytest.mark.parametrize("tp_prerequisite", [False, True])
async def test_durable_reply_never_confirms_or_discards_newer_pending_levels(monkeypatch, tp_prerequisite):
    entered, release = asyncio.Event(), asyncio.Event()
    record = IntentRecord(
        "old-intent", IntentState.DONE, "old-attempt", {"ticket": 12345},
        {"retcode": 10009}, 1, "2026-09-20T00:00:00+00:00",
    )
    service = _DurableServiceDouble(None)
    service.store = SimpleNamespace()

    async def execute(request, **kwargs):
        if tp_prerequisite and "tp-prerequisite" not in request.intent_key.leg:
            return DurableExecutionResult(
                IntentRecord("old-intent", IntentState.PREPARED, None, {}, None, 0, None),
                ExecutionDisposition.NOT_SENT, None,
                predispatch_error="requested_sl_waits_for_market",
            )
        entered.set()
        await release.wait()
        return DurableExecutionResult(record, ExecutionDisposition.APPLIED, {"state": "DONE"})

    service.execute = execute
    queue = PendingQueue(execution_service=service)
    monkeypatch.setattr(queue, "_ensure_runner", lambda: None)
    monkeypatch.setattr(queue, "_log_done", lambda *a: None)
    old = _make_action(new_sl=2490.0, new_tp=2510.0)
    queue.add(old)
    task = asyncio.create_task(queue._try_once(old))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        queue.add(_make_action(new_sl=2495.0, new_tp=2520.0))
        release.set()
        assert await task == "RETRY"
        assert old.new_sl == 2495.0 and old.new_tp == 2520.0
        assert old in queue._actions
        assert old.signal.tp_by_ticket == {12345: 2510.0}
        assert old.signal.sl_by_ticket == ({} if tp_prerequisite else {12345: 2490.0})
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_transport_confirms_old_levels_then_sends_new_revision(tmp_path, monkeypatch):
    from functools import partial
    import json
    from durable_execution import DurableExecutionService
    from mt5_client import MT5ReadClient
    from mt5_protocol import LookupState
    from mt5_worker import WorkerConfig
    from tests.mt5_load_fakes import PendingRevisionMT5

    old = _make_action(new_sl=2490.0, new_tp=2510.0)
    old.signal.status = "open"
    path = tmp_path / "orders.jsonl"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(PendingRevisionMT5, magic=old.signal.magic, order_log=str(path)),
        store_path=tmp_path / "intents.sqlite3",
    )
    queue = PendingQueue(tmp_path / "pending.json", execution_service=DurableExecutionService(client))
    monkeypatch.setattr(queue, "_ensure_runner", lambda: None)
    monkeypatch.setattr(queue, "_log_done", lambda *a: None)
    queue.add(old)
    task = None
    try:
        assert (await client.start()).state is LookupState.FOUND
        task = asyncio.create_task(queue._try_once(old))
        deadline = asyncio.get_running_loop().time() + 2
        while not path.exists():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.01)
        await queue.persist_call(queue.add, _make_action(new_sl=2495.0, new_tp=2520.0))
        assert await task == "RETRY"
        assert old.signal.sl_by_ticket == {12345: 2490.0}
        assert old.signal.tp_by_ticket == {12345: 2510.0}
        assert await queue._try_once(old) == "DONE"
        orders = [json.loads(line) for line in path.read_text().splitlines()]
        assert [(row["sl"], row["tp"]) for row in orders] == [(2490.0, 2510.0), (2495.0, 2520.0)]
    finally:
        await client.close()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
