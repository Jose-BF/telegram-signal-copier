import asyncio
from functools import partial

import pytest

from mt5_client import MT5ReadClient
from mt5_protocol import LookupState
from mt5_runtime import MT5Runtime, mt5
from mt5_worker import WorkerConfig
from tests.mt5_read_fakes import FakeMT5, FakeTradeMT5


@pytest.fixture(autouse=True)
def _clear_runtime():
    mt5.install(None)
    yield
    mt5.install(None)


def _client(tmp_path, **backend_kwargs):
    return MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(FakeTradeMT5, **backend_kwargs),
        store_path=tmp_path / "execution.sqlite3",
    )


@pytest.mark.asyncio
async def test_runtime_exposes_native_shaped_reads_from_single_child(tmp_path):
    runtime = MT5Runtime(_client(tmp_path))
    response = await runtime.start()
    assert response.state is LookupState.FOUND

    positions = await asyncio.to_thread(mt5.positions_get)
    tick = await asyncio.to_thread(mt5.symbol_info_tick, "XAUUSD")
    account = await asyncio.to_thread(mt5.account_info)

    assert positions[0].ticket == 42
    assert (tick.bid, tick.ask) == (2500.0, 2500.2)
    assert (account.login, account.server, account.currency) == (7, "demo", "EUR")
    assert runtime.client.worker_pid is not None
    assert runtime.snapshot("positions_get")["worker_pid"] == runtime.client.worker_pid
    await runtime.close()
    assert not runtime.client.is_alive


@pytest.mark.asyncio
async def test_runtime_rejects_sync_read_on_owner_loop(tmp_path):
    runtime = MT5Runtime(_client(tmp_path))
    await runtime.start()
    try:
        with pytest.raises(RuntimeError, match="event loop"):
            mt5.positions_get()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_unknown_read_is_never_converted_to_empty(tmp_path):
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(FakeMT5, mode="none"),
        store_path=tmp_path / "execution.sqlite3",
    )
    runtime = MT5Runtime(client)
    assert (await runtime.start()).state is LookupState.FOUND
    try:
        positions = await asyncio.to_thread(mt5.positions_get)
        assert positions is None
        assert mt5.last_error()[1]
        snapshot = runtime.snapshot("positions_get")
        assert snapshot["state"] == "UNKNOWN"
        assert snapshot["error"]
    finally:
        await runtime.close()


def test_proxy_blocks_unowned_lifecycle_and_trade_calls():
    with pytest.raises(RuntimeError, match="owned by the isolated worker"):
        mt5.initialize()
    with pytest.raises(RuntimeError, match="durable execution"):
        mt5.order_send({"action": mt5.TRADE_ACTION_DEAL})


@pytest.mark.asyncio
async def test_history_by_position_and_tick_range_use_bounded_protocol(tmp_path):
    runtime = MT5Runtime(_client(tmp_path))
    await runtime.start()
    try:
        deals = await asyncio.to_thread(mt5.history_deals_get, position=42)
        ticks = await asyncio.to_thread(
            mt5.copy_ticks_range,
            "XAUUSD",
            1_700_000_000,
            1_700_000_001,
            mt5.COPY_TICKS_ALL,
        )
        assert deals is not None
        assert ticks is not None
    finally:
        await runtime.close()
