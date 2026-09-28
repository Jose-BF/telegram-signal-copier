import asyncio
from concurrent.futures import Future
from contextlib import asynccontextmanager
import threading
from types import SimpleNamespace

import pytest

import tests.runtime_replay_support as support


class Owner:
    def __init__(self):
        self.thread = threading.get_ident()
        self.runtime_identity = SimpleNamespace(symbol="XAUUSD")
        self.effects = []

    def _assert_owner(self):
        assert threading.get_ident() == self.thread

    def snapshot(self):
        self._assert_owner()
        return {"quote_index": 0, "time_ns": 1, "positions": [],
                "symbol": {"name": "XAUUSD"}, "tick": {"time_msc": 1}}

    def apply(self, request):
        self._assert_owner()
        self.effects.append(dict(request))
        return {"retcode": 10009}


@asynccontextmanager
async def mailbox(**kwargs):
    box = support.RuntimeMailbox(Owner(), **kwargs)
    try:
        yield box
    finally:
        await asyncio.wait_for(box.close(), 2)


async def stop_pump(box):
    box.task.cancel()
    await asyncio.gather(box.task, return_exceptions=True)


async def wait_event(event):
    assert await asyncio.to_thread(event.wait, 2), "barrier was not reached"


async def test_call_retains_pending_without_internal_timeout(monkeypatch):
    waiting = threading.Event()
    timeouts = []

    class ObservedFuture(Future):
        def result(self, timeout=None):
            timeouts.append(timeout)
            waiting.set()
            return super().result(timeout)

    monkeypatch.setattr(support, "Future", ObservedFuture)
    async with mailbox() as box:
        await stop_pump(box)
        task = asyncio.create_task(asyncio.to_thread(box.call, "orders_get"))
        try:
            await wait_event(waiting)
            assert timeouts == [None]
            assert len(box.pending) == 1 and not task.done()
        finally:
            box.abort()
            box.task = asyncio.create_task(box._pump())
            with pytest.raises(RuntimeError, match="aborted"):
                await asyncio.wait_for(task, 2)
        assert not box.pending


async def test_abort_before_pump_prevents_queued_effect():
    async with mailbox() as box:
        await stop_pump(box)
        tasks = [asyncio.create_task(asyncio.to_thread(box.call, "order_send", {"action": 6}))
                 for _ in range(3)]
        try:
            calls = [await asyncio.wait_for(box.inbox.get(), 2) for _ in tasks]
            for call in calls:
                box.inbox.put_nowait(call)
            box.abort()
            box.abort()
            box.task = asyncio.create_task(box._pump())
            for task in tasks:
                with pytest.raises(RuntimeError, match="aborted"):
                    await asyncio.wait_for(task, 2)
            await asyncio.wait_for(box.task, 2)
            assert box.owner.effects == [] and box.records == []
            assert not box.pending
            assert box.inbox.empty(), "idempotent abort must enqueue one sentinel"
        finally:
            box.abort()
            await asyncio.gather(*tasks, return_exceptions=True)


class ContendedLock:
    def __init__(self):
        self.lock = threading.Lock()
        self.contended = threading.Event()

    def __enter__(self):
        if not self.lock.acquire(blocking=False):
            self.contended.set()
            assert self.lock.acquire(timeout=2), "finalization lock did not release"
        return self

    def __exit__(self, *args):
        self.lock.release()


@pytest.mark.parametrize("first,other", [
    ("reply", "abort"), ("abort", "reply"),
    ("error", "abort"), ("abort", "error"),
])
async def test_response_and_abort_linearize_under_same_lock(first, other):
    entered, release = threading.Event(), threading.Event()

    class PausingFuture(Future):
        def __init__(self):
            super().__init__()
            self.pause = True

        def done(self):
            result = super().done()
            if self.pause:
                self.pause = False
                entered.set()
                assert release.wait(3), "test did not release finalization"
            return result

    async with mailbox() as box:
        future = PausingFuture()
        box.pending.add(future)
        box.lock = lock = ContendedLock()
        operations = {"reply": lambda: box._reply(future, {"ok": True}),
                      "error": lambda: box._fail(future, ValueError("pump error")),
                      "abort": box.abort}
        first_task = asyncio.create_task(asyncio.to_thread(operations[first]))
        second_task = None
        try:
            await wait_event(entered)
            second_task = asyncio.create_task(asyncio.to_thread(operations[other]))
            await wait_event(lock.contended)
        finally:
            release.set()
            tasks = [first_task] + ([second_task] if second_task is not None else [])
            await asyncio.wait_for(asyncio.gather(*tasks), 2)
        if first == "reply":
            assert future.result() == {"ok": True}
        elif first == "error":
            with pytest.raises(ValueError, match="pump error"):
                future.result()
        else:
            with pytest.raises(RuntimeError, match="aborted"):
                future.result()
        assert box.closed
        await asyncio.wait_for(box.task, 2)


@pytest.mark.parametrize("hold", ["hold_responses", "hold_preparations"])
async def test_abort_unblocks_held_call_without_repeating_effect(hold):
    async with mailbox() as box:
        setattr(box, hold, True)
        committed = hold == "hold_responses"
        name = "order_send" if committed else "prepared"
        task = asyncio.create_task(asyncio.to_thread(box.call, name, {"action": 6}))
        try:
            await asyncio.wait_for((box.applied if committed else box.prepared).get(), 2)
            assert len(box.pending) == 1
            box.abort()
            with pytest.raises(RuntimeError, match="aborted"):
                await asyncio.wait_for(task, 2)
            box.release_responses()
            box.release_preparations()
            box.abort()
            assert len(box.owner.effects) == int(committed)
            assert not box.pending
            with pytest.raises(RuntimeError, match="closed"):
                await asyncio.to_thread(box.call, "order_send", {"action": 6})
        finally:
            box.abort()
            await asyncio.gather(task, return_exceptions=True)


async def test_owner_thread_cannot_make_blocking_call():
    async with mailbox() as box:
        with pytest.raises(RuntimeError, match="block replay owner"):
            box.call("positions_get")
        assert not box.pending and not box.records


@pytest.mark.parametrize("name", ["history_deals_get", "history_orders_get"])
async def test_unimplemented_history_is_not_reported_as_known_empty(name):
    async with mailbox() as box:
        with pytest.raises(ValueError, match="history is not implemented"):
            await asyncio.to_thread(box.call, name)
        assert not box.owner.effects and not box.pending


@pytest.mark.parametrize("budget", [0, 1, 3])
async def test_budget_limits_effects_and_records(budget):
    async with mailbox(max_calls=budget) as box:
        for _ in range(budget):
            assert await asyncio.to_thread(box.call, "order_send", {"action": 6}) == {"retcode": 10009}
        with pytest.raises(RuntimeError, match="budget exhausted"):
            await asyncio.to_thread(box.call, "order_send", {"action": 6})
        assert len(box.records) == len(box.owner.effects) == budget
        assert not box.pending


async def test_pump_errors_finalize_with_mailbox_lock(monkeypatch):
    async with mailbox() as box:
        locks = []

        class CheckedFuture(Future):
            def set_exception(self, exception):
                locks.append(box.lock.locked())
                return super().set_exception(exception)

        monkeypatch.setattr(support, "Future", CheckedFuture)
        with pytest.raises(ValueError, match="unsupported"):
            await asyncio.to_thread(box.call, "unsupported")
        assert locks == [True]
        assert not box.pending
