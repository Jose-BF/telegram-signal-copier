import asyncio
from dataclasses import replace
from functools import partial

import pytest

from mt5_client import MT5ReadClient
from mt5_protocol import BrokerRequest, IntentKey, IntentState, LookupState
from mt5_read_protocol import ReadOperation, ReadRequest
from mt5_worker import WorkerConfig
from tests.mt5_load_fakes import OrderedTradeMT5
from tests.test_mt5_trade_client import trade_request


@pytest.mark.asyncio
async def test_management_burst_preserves_read_fairness(tmp_path):
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=OrderedTradeMT5,
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3
    order = []
    tasks = []

    async def acquire_and_release(kind):
        assert await client._acquire_transport(kind, deadline)
        order.append(kind)
        await asyncio.sleep(0)
        await client._release_transport(kind)

    try:
        assert await client._acquire_transport("read", deadline)
        tasks = [asyncio.create_task(acquire_and_release(kind)) for kind in [
            "trade", *(["management"] * 5), "read",
        ]]
        while sum(client._transport_waiters.values()) != len(tasks):
            assert loop.time() < deadline
            await asyncio.sleep(0)
        await client._release_transport("read")
        await asyncio.gather(*tasks)
        assert order == [*(["management"] * 4), "read", "management", "trade"]
        assert not client.transport_snapshot()["active"]
        assert sum(client._transport_waiters.values()) == 0
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.close()


def test_management_reserve_is_one_bounded_slot():
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)), queue_size=2,
        backend_factory=OrderedTradeMT5,
    )
    assert client._admit("entry-1")
    assert client._admit("entry-2")
    assert not client._admit("entry-3")
    assert client._admit("close-1", management=True)
    assert not client._admit("close-2", management=True)
    assert client.pending_count == client.transport_snapshot()["capacity"] == 3
    for request_id in ("entry-1", "entry-2", "close-1"):
        client._withdraw(request_id)
    assert client.pending_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_operation", ["OPEN_MARKET", "PLACE_LIMIT"])
async def test_close_reserves_capacity_and_overtakes_queued_entry(tmp_path, entry_operation):
    log = tmp_path / "order.txt"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)), queue_size=2,
        backend_factory=partial(OrderedTradeMT5, order_log=str(log)),
        store_path=tmp_path / "intents.sqlite3",
    )
    tasks = []
    try:
        assert (await client.start()).state is LookupState.FOUND
        tasks.append(asyncio.create_task(client.read(ReadRequest(ReadOperation.TICK, {"symbol": "XAUUSD"}), timeout=3)))
        deadline = asyncio.get_running_loop().time() + 2
        while not client.transport_snapshot()["active"]:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.001)
        entry_request = trade_request()
        if entry_operation == "PLACE_LIMIT":
            payload = dict(entry_request.payload)
            payload.pop("loss_budget")
            payload.pop("protection_policy")
            payload.update(price=2499.0, sl=2498.0)
            entry_request = BrokerRequest.create(
                replace(entry_request.intent_key, operation=entry_operation), payload,
            )
        tasks.append(asyncio.create_task(client.execute(
            entry_request, reservation_key="entry", policy_revision=0, timeout=3,
        )))
        while client.transport_snapshot()["trade_waiters"] < 1:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.001)
        request = BrokerRequest.create(
            IntentKey("demo/7", "canal1", "canal1_3086", 1, "ticket-42", "CLOSE_POSITION", 0),
            {"symbol": "XAUUSD", "ticket": 42, "expected_magic": 111, "deviation": 30},
        )
        tasks.append(asyncio.create_task(client.execute(request, reservation_key="close", policy_revision=0, timeout=3)))
        _, entry, close = await asyncio.gather(*tasks)
        assert close.state is IntentState.DONE and entry.state is IntentState.DONE
        assert log.read_text(encoding="ascii").splitlines() == ["close", "entry"]
    finally:
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.close()
