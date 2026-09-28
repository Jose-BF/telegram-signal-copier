import asyncio
from functools import partial

import pytest

import main
from mt5_client import MT5ReadClient
from mt5_protocol import IntentState, LookupState
from mt5_runtime import MT5Runtime
from mt5_worker import WorkerConfig
from tests.mt5_read_fakes import FakeTradeMT5
from tests.test_mt5_trade_client import trade_request, wait_for_marker


@pytest.mark.asyncio
@pytest.mark.parametrize("notification_fails", [False, True])
async def test_stalled_native_worker_stops_session_without_waiting_for_telegram(tmp_path, monkeypatch, notification_fails):
    marker = tmp_path / "send.txt"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(FakeTradeMT5, marker=str(marker), trade_delay=60, trade_native=True),
        store_path=tmp_path / "intents.sqlite3",
    )
    runtime = MT5Runtime(client)
    cancelled = asyncio.Event()

    async def telegram():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(main, "_run_until_disconnected_with_backoff", telegram)
    monkeypatch.setattr(main.listener, "install_durable_execution_service", lambda *a: None)
    def anomaly(*args, **kwargs):
        if notification_fails:
            raise OSError("offline notification failure")

    monkeypatch.setattr(main.journal, "anomaly", anomaly)
    request = trade_request()
    order = None
    try:
        assert (await runtime.start()).state is LookupState.FOUND
        order = asyncio.create_task(client.execute(
            request, reservation_key="stalled-native", policy_revision=0, timeout=3,
        ))
        await wait_for_marker(marker)
        with pytest.raises(ConnectionError, match="transport_stalled") as failure:
            await asyncio.wait_for(main._run_connected_with_owner_supervision(
                runtime, check_interval=.01, stall_timeout=.05,
            ), 4)
        assert main._recoverable_connection_error(failure.value)
        assert cancelled.is_set()
        assert not client.is_alive
        await order
        assert client.store.get(request.intent_id).state is IntentState.UNKNOWN
        assert marker.read_text().splitlines() == ["send"]
    finally:
        await runtime.close()
        if order is not None:
            await asyncio.gather(order, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("alive", [False, True])
async def test_owner_death_is_recoverable_but_idle_market_is_not_a_stall(monkeypatch, alive):
    from types import SimpleNamespace

    closed = []
    runtime = SimpleNamespace(client=SimpleNamespace(
        is_alive=alive, worker_pid=123, session_id="offline",
        transport_snapshot=lambda: {"active": False, "active_age_seconds": 0.0},
    ))

    async def stop(owner):
        assert owner is runtime
        closed.append(owner)

    async def telegram():
        await asyncio.sleep(.03)

    monkeypatch.setattr(main, "_stop_mt5_owner", stop)
    monkeypatch.setattr(main, "_run_until_disconnected_with_backoff", telegram)
    monkeypatch.setattr(main.journal, "anomaly", lambda *a, **k: None)
    if alive:
        await main._run_connected_with_owner_supervision(runtime, check_interval=.001, stall_timeout=.005)
        assert not closed
    else:
        with pytest.raises(ConnectionError, match="worker_dead"):
            await main._run_connected_with_owner_supervision(runtime, check_interval=.001, stall_timeout=.005)
        assert closed == [runtime]
