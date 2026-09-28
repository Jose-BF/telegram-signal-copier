import asyncio
from dataclasses import replace
from functools import partial
import json
import sqlite3

import pytest

from execution_intents import OutcomePersistenceError
from mt5_client import MT5ReadClient
from mt5_protocol import BrokerRequest, IntentKey, IntentState, LookupState
from mt5_read_protocol import ReadOperation as Op, ReadRequest
from mt5_worker import WorkerConfig
from tests.mt5_read_fakes import FakeTradeMT5, PriorityProbeMT5


async def wait_for_marker(marker, *, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not marker.exists():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("native order_send did not start")
        await asyncio.sleep(0.01)


def trade_request(
    *,
    attempt="attempt-1",
    revision=0,
    signal_root="canal1_3086",
):
    key = IntentKey(
        "demo/7", "canal1", signal_root, 1, "entry-1", "OPEN_MARKET", revision
    )
    return BrokerRequest.create(
        key,
        {
            "symbol": "XAUUSD",
            "direction": "BUY",
            "volume": 0.01,
            "sl": 2499.0,
            "tp": 2502.0,
            "loss_budget": None,
            "protection_policy": "required",
            "magic": 111,
            "comment": "c1_3086_1",
            "deviation": 30,
        },
        request_id=f"request-{attempt}",
        attempt_id=attempt,
        action_id="action-1",
    )


def client(tmp_path, **backend_kwargs):
    return MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(FakeTradeMT5, **backend_kwargs),
        store_path=tmp_path / "intents.sqlite3",
    )


@pytest.mark.asyncio
async def test_trade_overtakes_queued_read_after_active_read_finishes(tmp_path):
    marker = tmp_path / "transport-order.txt"
    c = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(PriorityProbeMT5, marker=str(marker)),
        store_path=tmp_path / "priority.sqlite3",
    )
    assert (await c.start()).state is LookupState.FOUND
    active = asyncio.create_task(c.read(
        ReadRequest(Op.TICK, {"symbol": "XAUUSD"}),
        timeout=1.0,
    ))
    await asyncio.sleep(0.03)
    queued_read = asyncio.create_task(c.read(
        ReadRequest(Op.POSITIONS),
        timeout=1.0,
    ))
    queued_trade = asyncio.create_task(c.execute(
        trade_request(),
        reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
        policy_revision=0,
        timeout=1.0,
    ))
    try:
        await asyncio.gather(active, queued_read, queued_trade)
    finally:
        await c.close()

    assert marker.read_text(encoding="ascii").splitlines() == [
        "trade",
        "read",
    ]


@pytest.mark.asyncio
async def test_trade_priority_yields_to_waiting_read_after_bounded_burst(tmp_path):
    marker = tmp_path / "fair-transport-order.txt"
    c = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(
            PriorityProbeMT5,
            marker=str(marker),
            delay=0.3,
        ),
        store_path=tmp_path / "fair-priority.sqlite3",
        queue_size=8,
    )
    assert (await c.start()).state is LookupState.FOUND
    active = asyncio.create_task(c.read(
        ReadRequest(Op.TICK, {"symbol": "XAUUSD"}),
        timeout=2.0,
    ))
    await asyncio.sleep(0.03)
    queued_read = asyncio.create_task(c.read(
        ReadRequest(Op.POSITIONS),
        timeout=2.0,
    ))
    trades = [
        asyncio.create_task(c.execute(
            trade_request(
                attempt=f"fair-{index}",
                signal_root=f"canal1_{4000 + index}",
            ),
            reservation_key=(
                f"demo/7/canal1/canal1_{4000 + index}/g1/entry-1"
            ),
            policy_revision=0,
            timeout=4.0,
        ))
        for index in range(5)
    ]
    try:
        deadline = asyncio.get_running_loop().time() + 2.0
        while (
            c.transport_snapshot()["trade_waiters"] < 5
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.005)
        await asyncio.gather(active, queued_read, *trades)
    finally:
        await c.close()

    order = marker.read_text(encoding="ascii").splitlines()
    assert order.count("trade") == 5
    assert order.count("read") == 1
    assert order.index("read") == 4


@pytest.mark.asyncio
async def test_two_phase_client_persists_exact_request_before_single_send(tmp_path):
    marker = tmp_path / "sends.txt"
    c = client(tmp_path, marker=str(marker))
    assert (await c.start()).state is LookupState.FOUND
    request = trade_request()
    try:
        record = await c.execute(
            request,
            reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
            policy_revision=0,
        )
    finally:
        await c.close()

    assert record.state is IntentState.DONE
    assert record.outcome["deal"] == 702
    assert marker.read_text(encoding="ascii").splitlines() == ["send"]
    with sqlite3.connect(tmp_path / "intents.sqlite3") as connection:
        prepared = json.loads(connection.execute(
            "SELECT prepared_payload_json FROM execution_attempts WHERE attempt_id = ?",
            (request.attempt_id,),
        ).fetchone()[0])
    assert prepared["price"] == 2500.2
    assert prepared["sl"] == 2499.0
    assert prepared["magic"] == 111


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trade_mode", "state"),
    [
        ("none", IntentState.UNKNOWN),
        ("placed", IntentState.PLACED),
        ("partial", IntentState.DONE_PARTIAL),
    ],
)
async def test_unresolved_outcome_is_never_resent_on_redelivery(tmp_path, trade_mode, state):
    marker = tmp_path / "sends.txt"
    c = client(tmp_path, marker=str(marker), trade_mode=trade_mode)
    assert (await c.start()).state is LookupState.FOUND
    first = trade_request()
    try:
        result = await c.execute(
            first,
            reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
            policy_revision=0,
        )
        redelivery = await c.execute(
            trade_request(attempt="attempt-2"),
            reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
            policy_revision=0,
        )
    finally:
        await c.close()

    assert result.state is state
    assert redelivery.state is state
    assert marker.read_text(encoding="ascii").splitlines() == ["send"]


@pytest.mark.asyncio
async def test_dispatch_guard_aborts_prepared_trade_without_send(tmp_path):
    marker = tmp_path / "sends.txt"
    c = client(tmp_path, marker=str(marker))
    assert (await c.start()).state is LookupState.FOUND

    def stale_generation(_request):
        raise RuntimeError("generation changed")

    try:
        record = await c.execute(
            trade_request(),
            reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
            policy_revision=0,
            dispatch_guard=stale_generation,
        )
    finally:
        await c.close()

    assert record.state is IntentState.PREPARED
    assert not marker.exists()


@pytest.mark.asyncio
async def test_slow_native_send_does_not_block_loop_and_late_result_is_durable(tmp_path):
    marker = tmp_path / "sends.txt"
    c = client(tmp_path, marker=str(marker), trade_delay=0.2, trade_native=True)
    assert (await c.start()).state is LookupState.FOUND
    beats = 0

    async def heartbeat():
        nonlocal beats
        end = asyncio.get_running_loop().time() + 0.12
        while asyncio.get_running_loop().time() < end:
            beats += 1
            await asyncio.sleep(0.01)

    try:
        request = trade_request()
        execution = asyncio.create_task(c.execute(
            request,
            reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
            policy_revision=0,
            timeout=2.0,
        ))
        heartbeat_task = asyncio.create_task(heartbeat())
        await wait_for_marker(marker)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
        await heartbeat_task
        record = c.store.get(request.intent_id)
        assert record.state is IntentState.DISPATCHING
        while c.pending_count:
            await asyncio.sleep(0.02)
        durable = c.store.get(request.intent_id)
    finally:
        await c.close()

    assert beats >= 5
    assert durable.state is IntentState.DONE
    assert marker.read_text(encoding="ascii").splitlines() == ["send"]


@pytest.mark.asyncio
async def test_internal_timeout_keeps_late_broker_result_durable(tmp_path):
    marker = tmp_path / "timeout-sends.txt"
    c = client(tmp_path, marker=str(marker), trade_delay=1.5)
    assert (await c.start()).state is LookupState.FOUND
    request = trade_request()
    try:
        execution = asyncio.create_task(c.execute(
            request,
            reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
            policy_revision=0,
            timeout=1.0,
        ))
        await wait_for_marker(marker, timeout=0.8)
        timed_out = await execution
        assert timed_out.state is IntentState.DISPATCHING
        while c.pending_count:
            await asyncio.sleep(0.02)
        durable = c.store.get(request.intent_id)
    finally:
        await c.close()

    assert durable.state is IntentState.DONE
    assert marker.read_text(encoding="ascii").splitlines() == ["send"]


@pytest.mark.asyncio
async def test_confirmed_outcome_survives_parent_storage_failure(tmp_path, monkeypatch):
    marker = tmp_path / "sends.txt"
    c = client(tmp_path, marker=str(marker))
    assert (await c.start()).state is LookupState.FOUND
    original = c.store.record_outcome
    failed = False

    def fail_once(request, outcome, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise sqlite3.OperationalError("disk unavailable")
        return original(request, outcome, **kwargs)

    monkeypatch.setattr(c.store, "record_outcome", fail_once)
    try:
        with pytest.raises(OutcomePersistenceError):
            await c.execute(
                trade_request(),
                reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
                policy_revision=0,
            )
        assert c.retained_trade_outcome_count == 1
        assert c.store.get(trade_request().intent_id).state is IntentState.DISPATCHING
        assert await c.recover_trade_outcomes() == 1
        durable = c.store.get(trade_request().intent_id)
    finally:
        await c.close()

    assert durable.state is IntentState.DONE
    assert c.retained_trade_outcome_count == 0
    assert marker.read_text(encoding="ascii").splitlines() == ["send"]


@pytest.mark.asyncio
async def test_worker_death_inside_order_send_is_unknown_and_never_resent(tmp_path):
    marker = tmp_path / "sends.txt"
    c = client(tmp_path, marker=str(marker), trade_mode="die")
    assert (await c.start()).state is LookupState.FOUND
    request = trade_request()
    try:
        first = await c.execute(
            request,
            reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
            policy_revision=0,
        )
        redelivery = await c.execute(
            trade_request(attempt="attempt-after-worker-death"),
            reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
            policy_revision=0,
        )
    finally:
        await c.close()

    assert first.state is IntentState.UNKNOWN
    assert redelivery.state is IntentState.UNKNOWN
    assert marker.read_text(encoding="ascii").splitlines() == ["send"]


@pytest.mark.asyncio
async def test_queued_trade_timeout_is_durably_prepared_without_send(tmp_path):
    marker = tmp_path / "sends.txt"
    c = client(tmp_path, marker=str(marker), trade_delay=0.2, trade_native=True)
    assert (await c.start()).state is LookupState.FOUND
    first = trade_request()
    second_key = replace(
        first.intent_key,
        signal_root="canal1_4000",
        leg="entry-0",
    )
    second = BrokerRequest.create(
        second_key,
        dict(first.payload),
        request_id="request-queued",
        attempt_id="attempt-queued",
        action_id="action-queued",
    )
    try:
        first_task = asyncio.create_task(c.execute(
            first,
            reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
            policy_revision=0,
            timeout=1.0,
        ))
        while not marker.exists():
            await asyncio.sleep(0.01)
        queued = await c.execute(
            second,
            reservation_key="demo/7/canal1/canal1_4000/g1/entry-0",
            policy_revision=0,
            timeout=0.05,
        )
        first_result = await first_task
        while c.pending_count:
            await asyncio.sleep(0.01)
    finally:
        await c.close()

    assert first_result.state is IntentState.DONE
    assert queued.state is IntentState.PREPARED
    assert c.store.get(second.intent_id).state is IntentState.PREPARED
    assert marker.read_text(encoding="ascii").splitlines() == ["send"]
