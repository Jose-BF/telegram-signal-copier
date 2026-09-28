import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

import mt5_gateway
from durable_execution import DurableExecutionService
from mt5_read_protocol import ReadOperation, ReadRequest
from tests.test_mt5_gateway import _fake_gateway, _request
from tests.test_mt5_read_client import client
from tests.test_mt5_trade_client import trade_request


async def test_cancellation_concurrent_with_executor_start_is_not_swallowed(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    gateway._queue_size = 1
    gateway._async_admission = asyncio.Semaphore(1)
    admission_released = asyncio.Event()
    original_release = gateway._async_admission.release

    def record_release():
        original_release()
        admission_released.set()

    monkeypatch.setattr(gateway._async_admission, "release", record_release)
    release = threading.Event()
    cancellation_issued = asyncio.Event()
    completed = asyncio.Event()
    loop = asyncio.get_running_loop()
    calls = []

    def blocking_call(request, deadline):
        calls.append(request.request_id)
        try:
            if not release.wait(5):
                raise RuntimeError("test cleanup failed to release worker")
            return gateway.store.prepare(request)
        finally:
            loop.call_soon_threadsafe(completed.set)

    original = mt5_gateway._mark_future_done

    def mark_then_cancel(started):
        original(started)
        # shield's completion callback runs first; cancellation then reaches
        # wait_for while its inner future is done but before its waiter wakes.
        loop.call_soon(first.cancel)
        loop.call_soon(cancellation_issued.set)

    monkeypatch.setattr(gateway, "_call_until", blocking_call)
    monkeypatch.setattr(mt5_gateway, "_mark_future_done", mark_then_cancel)
    first = asyncio.create_task(gateway.call_async(_request(attempt_id="race"), timeout=2))
    try:
        await asyncio.wait_for(cancellation_issued.wait(), 2)
        for _ in range(10):
            await asyncio.sleep(0)
        assert first.done(), "external cancellation was swallowed while worker remained active"
        assert first.cancelled()
        assert gateway._async_admission._value == 0
        with pytest.raises(mt5_gateway.AdmissionTimeoutError):
            await gateway.call_async(_request(attempt_id="no-capacity"), timeout=.02)
        assert calls == ["request-race"]
    finally:
        release.set()
        await asyncio.wait_for(completed.wait(), 2)
        await asyncio.gather(first, return_exceptions=True)
        # The thread's finally runs before the executor completion callback.
        await asyncio.wait_for(admission_released.wait(), 2)
    assert gateway._async_admission._value == 1


async def test_cancellation_with_admission_grant_does_not_start_broker_work(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    gateway._async_admission = asyncio.Semaphore(0)
    calls = []

    def call(request, deadline):
        calls.append(request.request_id)
        return gateway.store.prepare(request)

    monkeypatch.setattr(gateway, "_call_until", call)
    first = asyncio.create_task(gateway.call_async(_request(), timeout=2))
    for _ in range(10):
        await asyncio.sleep(0)
        if gateway._async_admission._waiters:
            break
    assert gateway._async_admission._waiters
    gateway._async_admission.release()
    first.cancel()
    result = (await asyncio.gather(first, return_exceptions=True))[0]
    assert isinstance(result, asyncio.CancelledError)
    assert calls == []
    assert gateway._async_admission._value == 1


@pytest.mark.parametrize("kind", ["trade", "management", "read"])
async def test_cancellation_with_transport_notification_never_acquires(kind):
    c = client()
    c._bind_loop()
    c._transport_busy = True
    first = asyncio.create_task(c._acquire_transport(kind, time.monotonic() + 2))
    try:
        for _ in range(10):
            await asyncio.sleep(0)
            if c._transport_waiters[kind]:
                break
        assert c._transport_waiters[kind] == 1
        await c._release_transport("read")
        first.cancel()
        result = (await asyncio.gather(first, return_exceptions=True))[0]
        assert isinstance(result, asyncio.CancelledError)
        assert not c._transport_busy
        assert c._transport_waiters[kind] == 0
    finally:
        await c.close()


async def test_response_completion_does_not_swallow_read_cancellation(monkeypatch):
    c = client()
    c._bind_loop()
    loop = asyncio.get_running_loop()
    response_future = loop.create_future()
    submitted = asyncio.Event()
    request = ReadRequest(ReadOperation.POSITIONS)

    def execute(*args, **kwargs):
        submitted.set()
        return response_future

    try:
        with monkeypatch.context() as patch:
            patch.setattr(loop, "run_in_executor", execute)
            task = asyncio.create_task(c._submit(request, time.monotonic() + 2))
            await submitted.wait()
            response_future.set_result(c._unknown(request, "fixture_response"))
            loop.call_soon(task.cancel)
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert isinstance(result, asyncio.CancelledError)
            for _ in range(10):
                await asyncio.sleep(0)
            assert c.pending_count == 0
            assert not c._transport_busy
    finally:
        await c.close()


async def test_evidence_confirmation_and_cancellation_never_dispatch_entry():
    ready = asyncio.Event()
    confirmed = asyncio.get_running_loop().create_future()
    calls = []

    async def execute(*args, **kwargs):
        calls.append("dispatched")
        raise AssertionError("cancelled entry reached client execution")

    service = DurableExecutionService(SimpleNamespace(store=object(), execute=execute))

    async def evidence(*args, **kwargs):
        ready.set()
        return await confirmed

    service.entry_evidence_probe = evidence
    task = asyncio.create_task(service.execute(trade_request(), reservation_key="entry", projection_key="entry"))
    await ready.wait()
    confirmed.set_result(True)
    task.cancel()
    result = (await asyncio.gather(task, return_exceptions=True))[0]
    assert isinstance(result, asyncio.CancelledError)
    assert not calls
