import asyncio
import threading
import time

import pytest

import mt5_client
from mt5_protocol import IntentState
from tests.test_mt5_read_client import drain
from tests.test_mt5_trade_client import client, trade_request


@pytest.mark.parametrize("boundary", ["guard", "authorized"])
async def test_cancel_before_authorization_aborts_but_after_it_reconciles(tmp_path, monkeypatch, boundary):
    marker = tmp_path / "orders.txt"
    c = client(tmp_path, marker=str(marker))
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    request = trade_request()

    def hold():
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(5):
            raise RuntimeError("test barrier was not released")

    def guard(_request):
        if boundary == "guard":
            hold()

    original = c.store.begin_dispatch

    def begin(*args, **kwargs):
        if boundary == "authorized":
            hold()
        return original(*args, **kwargs)

    monkeypatch.setattr(c.store, "begin_dispatch", begin)
    try:
        await c.start()
        task = asyncio.create_task(c.execute(request, reservation_key="entry", policy_revision=0,
                                             dispatch_guard=guard, timeout=4))
        await asyncio.wait_for(entered.wait(), 2)
        assert c.store.get(request.intent_id).state is IntentState.PREPARED
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert c.pending_count == 1
        assert not marker.exists()
        release.set()
        await drain(c)
        for _ in range(10):
            if not c._transport_busy:
                break
            await asyncio.sleep(0)
        record = c.store.get(request.intent_id)
        if boundary == "guard":
            assert not marker.exists(), "a cancelled, not-yet-authorized order was sent"
            assert record.state is IntentState.PREPARED
            assert c.store.get_predispatch_failure(request.request_id) == "trade_cancelled_or_expired_before_authorization"
        else:
            assert len(marker.read_text(encoding="ascii").splitlines()) == 1
            assert record.state is IntentState.DONE
        assert not c._transport_busy
    finally:
        release.set()
        await c.close()


def test_cancelled_dispatch_cannot_later_authorize():
    decision = mt5_client._DispatchDecision()
    decision.set()
    assert not decision.authorize(time.monotonic() + 2, threading.Event())


def test_authorized_dispatch_is_not_reauthorized_after_caller_leaves():
    decision = mt5_client._DispatchDecision()
    assert decision.authorize(time.monotonic() + 2, threading.Event())
    decision.set()
    assert decision.is_set()
    with pytest.raises(RuntimeError, match="already authorized"):
        decision.authorize(time.monotonic() + 2, threading.Event())


@pytest.mark.parametrize("expired", [True, False])
def test_closed_or_expired_dispatch_is_not_authorized(expired):
    decision = mt5_client._DispatchDecision()
    stopped = threading.Event()
    if not expired:
        stopped.set()
    assert not decision.authorize(time.monotonic() + (-1 if expired else 2), stopped)
