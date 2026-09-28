from functools import partial
import asyncio

import pytest

from durable_execution import DurableExecutionService, ExecutionDisposition
from mt5_client import MT5ReadClient
from mt5_protocol import IntentState, LookupState
from mt5_worker import WorkerConfig
from tests.mt5_load_fakes import OrderedTradeMT5
from tests.test_mt5_trade_client import trade_request


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["canal1", "canal2"])
@pytest.mark.parametrize("operation", ["OPEN_MARKET", "PLACE_LIMIT"])
async def test_entry_evidence_guard_is_checked_before_native_send(tmp_path, channel, operation):
    marker = tmp_path / "orders.txt"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(OrderedTradeMT5, order_log=str(marker)),
        store_path=tmp_path / "intents.sqlite3",
    )
    service = DurableExecutionService(client)
    calls = []

    def evidence_unavailable(request):
        calls.append(request.intent_id)
        raise RuntimeError("journal unavailable")

    service.entry_guard = evidence_unavailable
    try:
        assert (await client.start()).state is LookupState.FOUND
        payload = dict(trade_request().payload)
        if operation == "PLACE_LIMIT":
            payload.pop("loss_budget")
            payload.pop("protection_policy")
            payload.update(price=2499.0, sl=2498.0)
        request = service.request(
            channel=channel, signal_root=f"{channel}_1", generation=0,
            leg="entry", operation=operation, revision=0, payload=payload,
        )
        result = await service.execute(request, reservation_key="entry", projection_key="entry")
        assert result.record.state is IntentState.PREPARED
        assert result.disposition is ExecutionDisposition.NOT_SENT
        assert calls == [request.intent_id]
        assert not marker.exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_successful_probe_preserves_original_dispatch_guard(tmp_path):
    marker = tmp_path / "orders.txt"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(OrderedTradeMT5, order_log=str(marker)),
        store_path=tmp_path / "intents.sqlite3",
    )
    service = DurableExecutionService(client)
    calls = []

    async def probe(request, *, timeout):
        calls.append("evidence")
        return True

    def guard(request):
        calls.append("original")
        raise RuntimeError("signal expired")

    service.entry_evidence_probe = probe
    service.entry_guard = lambda request: calls.append("health")
    try:
        assert (await client.start()).state is LookupState.FOUND
        result = await service.execute(
            trade_request(), reservation_key="entry", projection_key="entry", dispatch_guard=guard,
        )
        assert result.disposition is ExecutionDisposition.NOT_SENT
        assert calls == ["evidence", "health", "original"]
        assert not marker.exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_entry_evidence_timeout_never_dispatches(tmp_path):
    marker = tmp_path / "orders.txt"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(OrderedTradeMT5, order_log=str(marker)),
        store_path=tmp_path / "intents.sqlite3",
    )
    service = DurableExecutionService(client)
    cancelled = asyncio.Event()

    async def probe(request, *, timeout):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    service.entry_evidence_probe = probe
    try:
        assert (await client.start()).state is LookupState.FOUND
        result = await service.execute(
            trade_request(), reservation_key="entry", projection_key="entry", timeout=.3,
        )
        assert result.disposition is ExecutionDisposition.NOT_SENT
        assert cancelled.is_set()
        assert not marker.exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_evidence_guard_does_not_disable_protection_or_closing(tmp_path):
    marker = tmp_path / "orders.txt"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(OrderedTradeMT5, order_log=str(marker)),
        store_path=tmp_path / "intents.sqlite3",
    )
    service = DurableExecutionService(client)

    def evidence_unavailable(request):
        raise AssertionError("management must retain its durable execution path")

    service.entry_guard = evidence_unavailable
    try:
        assert (await client.start()).state is LookupState.FOUND
        for operation, extra in (
            ("MODIFY_SLTP", {"new_sl": 2499.0, "new_tp": 2502.0}),
            ("CLOSE_POSITION", {"deviation": 30}),
        ):
            request = service.request(
                channel="canal1", signal_root="canal1_1", generation=0,
                leg="position-42", operation=operation, revision=0,
                payload={"ticket": 42, "symbol": "XAUUSD", "expected_magic": 111, **extra},
            )
            result = await service.execute(
                request, reservation_key=operation, projection_key=operation,
            )
            assert result.record.state is IntentState.DONE
        assert len(marker.read_text().splitlines()) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_slow_entry_evidence_does_not_hold_the_management_transport(tmp_path):
    marker = tmp_path / "orders.txt"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(OrderedTradeMT5, order_log=str(marker)),
        store_path=tmp_path / "intents.sqlite3",
    )
    service = DurableExecutionService(client)
    started, release = asyncio.Event(), asyncio.Event()

    async def probe(request, *, timeout):
        started.set()
        await release.wait()
        return False

    service.entry_evidence_probe = probe
    entry = None
    try:
        assert (await client.start()).state is LookupState.FOUND
        entry = asyncio.create_task(service.execute(
            trade_request(), reservation_key="entry", projection_key="entry",
        ))
        await asyncio.wait_for(started.wait(), timeout=2)
        close = service.request(
            channel="canal1", signal_root="canal1_1", generation=0,
            leg="42", operation="CLOSE_POSITION", revision=0,
            payload={"ticket": 42, "symbol": "XAUUSD", "expected_magic": 111, "deviation": 30},
        )
        result = await service.execute(close, reservation_key="close", projection_key="close")
        assert result.record.state is IntentState.DONE
        assert not entry.done()
        release.set()
        assert (await entry).disposition is ExecutionDisposition.NOT_SENT
        assert marker.read_text().splitlines() == ["close"]
    finally:
        release.set()
        if entry is not None:
            await asyncio.gather(entry, return_exceptions=True)
        await client.close()
