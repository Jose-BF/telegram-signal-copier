import asyncio
from functools import partial
import threading

import pytest

from mt5_client import MT5ReadClient
from mt5_protocol import IntentState, LookupState
from mt5_worker import WorkerConfig
from tests.mt5_read_fakes import FakeTradeMT5
from tests.test_mt5_trade_client import trade_request


@pytest.mark.asyncio
async def test_cancelled_prepare_keeps_capacity_until_disk_work_finishes(tmp_path, monkeypatch):
    marker = tmp_path / "send.txt"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)), queue_size=1,
        backend_factory=partial(FakeTradeMT5, marker=str(marker)),
        store_path=tmp_path / "intents.sqlite3",
    )
    entered, release = threading.Event(), threading.Event()
    original = client.store.prepare_reserved
    task = None

    def slow_prepare(*args, **kwargs):
        entered.set()
        assert release.wait(5), "test failed to release storage worker"
        return original(*args, **kwargs)

    monkeypatch.setattr(client.store, "prepare_reserved", slow_prepare)
    request = trade_request()
    try:
        assert (await client.start()).state is LookupState.FOUND
        task = asyncio.create_task(client.execute(
            request, reservation_key="cancelled-storage", policy_revision=0,
        ))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.pending_count == 1, "unfinished disk work released capacity"
        for index in range(5):
            with pytest.raises(TimeoutError, match="capacity"):
                await client.execute(
                    trade_request(attempt=f"repeated-{index}"),
                    reservation_key="cancelled-storage", policy_revision=0,
                )
        assert not marker.exists()
        release.set()
        deadline = asyncio.get_running_loop().time() + 2
        while client.pending_count:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.01)
        assert client.store.get(request.intent_id).state is IntentState.PREPARED
        assert not marker.exists(), "cancellation must not dispatch in the background"
    finally:
        release.set()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await client.close()
