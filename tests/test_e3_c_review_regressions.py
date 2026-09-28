"""End-to-end offline controls for the E3-C integration review."""

import asyncio
import time
from datetime import datetime, timezone
from functools import partial
from types import SimpleNamespace

import pytest

import main
import config
import gold_555_live_candidate
import listener
import position_lifecycle_monitor as monitor
from durable_entry_execution import DurableEntryExecutor, EntryDispatchState
from durable_execution import DurableExecutionService
from execution_intents import IntentStore
from mt5_client import MT5ReadClient
from mt5_protocol import LookupState
from mt5_read_protocol import ReadOperation, ReadRequest
from mt5_runtime import MT5Runtime, mt5
from mt5_worker import ReadWorker, WorkerConfig
from state import StateManager
from tests.mt5_e3_c_review_fakes import ReviewBoundaryMT5
from tests.mt5_read_fakes import FakeTradeMT5
from tests.test_strategy_shadow_main import money_contract
from tests.test_dubai_live_ladder import _candidate_signal


def _runtime(tmp_path):
    return MT5Runtime(MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=ReviewBoundaryMT5,
        store_path=tmp_path / "review.sqlite3",
    ))


@pytest.mark.asyncio
async def test_filtered_empty_position_read_cannot_publish_account_flat(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(main, "pending_entry_count", lambda: 0)
    monkeypatch.setattr(main.config, "magic_for", lambda _channel: 20260422)
    try:
        assert (await runtime.start()).state is LookupState.FOUND
        all_positions = await runtime.read(ReadOperation.POSITIONS)
        assert len(all_positions.value) == 1
        missing_ticket = await runtime.read(ReadOperation.POSITIONS, {"ticket": 999})
        assert missing_ticket.state is LookupState.EMPTY
        exposure = main._runtime_exposure_snapshot(StateManager(), runtime=runtime)
        assert exposure["exposure_state"] == "open", exposure
        assert exposure["bot_position_count"] == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_shadow_history_keeps_last_fractional_second_through_real_proxy(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(main, "_load_shadow_money_contract", lambda: money_contract("identity"))
    try:
        assert (await runtime.start()).state is LookupState.FOUND
        history = await asyncio.to_thread(main._shadow_tick_history, 20000, until_msc=20900)
        assert history.complete, history
        assert [tick.time_msc for tick in history.ticks] == [20750], history
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_production_owner_allows_required_money_conversion_symbol(tmp_path, monkeypatch):
    monkeypatch.setattr(main.config, "MT5_LOGIN", 7)
    monkeypatch.setattr(main.config, "MT5_SERVER", "demo")
    monkeypatch.setattr(main.config, "MT5_SYMBOL", "XAUUSD")
    monkeypatch.setattr(main.config, "BOT_EXECUTION_LEDGER_FILE", str(tmp_path / "review.sqlite3"))
    monkeypatch.setattr(main, "native_backend", ReviewBoundaryMT5)
    monkeypatch.setattr(main, "_load_shadow_money_contract", lambda: money_contract())
    runtime = main._build_mt5_owner()
    try:
        assert (await runtime.start()).state is LookupState.FOUND
        quote = await asyncio.to_thread(mt5.symbol_info_tick, "EURUSD")
        assert quote is not None, mt5.last_error()
    finally:
        await runtime.close()


def test_same_account_reconnection_can_be_observed_after_transient_disconnect():
    backend = ReviewBoundaryMT5()
    worker = ReadWorker(backend, WorkerConfig(7, "demo", ("XAUUSD",)), "review")

    def read(operation):
        return worker.handle(ReadRequest(operation), time.monotonic() + 5)

    assert read(ReadOperation.INITIALIZE).state is LookupState.FOUND
    backend.connected = False
    assert read(ReadOperation.TERMINAL).error == "terminal_disconnected"
    backend.connected = True
    recovered = read(ReadOperation.TERMINAL)
    assert recovered.state is LookupState.FOUND, recovered.error


@pytest.mark.asyncio
@pytest.mark.parametrize("aware_expiry", [False, True], ids=["live-naive-utc", "aware-utc"])
async def test_unsent_candidate_leg_can_retry_at_changed_quote(tmp_path, monkeypatch, aware_expiry):
    class PrepareOnlyClient:
        config = SimpleNamespace(expected_server="demo", expected_login=7)

        def __init__(self):
            self.store = IntentStore(tmp_path / "review.sqlite3")

        async def execute(self, request, **kwargs):
            return self.store.prepare_reserved(
                request, reservation_key=kwargs["reservation_key"],
                policy_revision=kwargs["policy_revision"],
                expires_utc=kwargs.get("expires_utc"),
            )

    service = DurableExecutionService(PrepareOnlyClient())
    monkeypatch.setattr(monitor, "_durable_entry_executor", DurableEntryExecutor(service, symbol="XAUUSD"))
    monkeypatch.setattr(monitor, "_journal_event", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_journal_anomaly", lambda *a, **k: None)

    async def quote_dependent_stop(_signal, *, new_volume, observed_price):
        return observed_price - 5

    monkeypatch.setattr(monitor, "_dubai_provisional_basket_stop", quote_dependent_stop)
    signal, _ = _candidate_signal("BUY")
    if aware_expiry:
        signal.candidate_entry_expires_at = signal.candidate_entry_expires_at.replace(tzinfo=timezone.utc)
    leg = {"index": 1, "volume": 0.04, "trigger_price": 4196.0}
    assert await monitor._open_candidate_leg(signal, leg, 4195.9) is None
    assert await monitor._open_candidate_leg(signal, leg, 4195.8) is None


@pytest.mark.asyncio
async def test_late_gold_fill_after_real_timeout_is_applied_exactly_once(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(config, "GOLD_555_MAX_PLANNED_LOTS_PER_SIGNAL", 0.16)
    marker = tmp_path / "gold-timeout-send.txt"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(
            FakeTradeMT5,
            marker=str(marker),
            trade_delay=1.5,
        ),
        store_path=tmp_path / "gold-timeout.sqlite3",
    )
    assert (await client.start()).state is LookupState.FOUND
    service = DurableExecutionService(client)
    adapter = DurableEntryExecutor(service, symbol="XAUUSD")
    policy = gold_555_live_candidate.Gold555Policy()
    observed = datetime.now(timezone.utc)
    watch = listener.gold_555_entry_watch.EntryWatch.new(
        "BUY",
        reference=2500.0,
        observed_at=observed,
        policy=policy,
    )
    watch.status = "confirmed"
    watch.confirmed_quote = 2500.4
    watch.confirmed_at = observed
    message_id = 388
    seed = asyncio.create_task(adapter.open_market(
        channel="canal2",
        signal_root=f"canal2_{message_id}",
        generation=0,
        leg="entry-0",
        revision=0,
        direction="BUY",
        volume=policy.entry_volumes[0],
        sl=policy.initial_stop("BUY", watch.confirmed_quote),
        tp=None,
        loss_budget=None,
        protection_policy="required",
        magic=config.magic_for("canal2"),
        comment=gold_555_live_candidate.market_comment(message_id),
        action_id="timeout-seed",
        expires_utc=watch.expires_at.isoformat(),
        timeout=1.0,
    ))
    deadline = asyncio.get_running_loop().time() + 0.8
    while not marker.exists():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("order_send did not start before timeout")
        await asyncio.sleep(0.01)
    try:
        initial = await seed
        assert initial.state is EntryDispatchState.RECONCILE
        while client.pending_count:
            await asyncio.sleep(0.02)
        assert adapter.reconstruct()[0].state is EntryDispatchState.CONFIRMED

        runtime_state = StateManager()
        listener._entry_execution_gate.reset()
        listener._canal2_opening_msg_ids.clear()
        listener._gold_555_entry_watches.clear()
        monkeypatch.setattr(listener, "state", runtime_state)
        monkeypatch.setattr(listener, "_durable_entry_executor", adapter)
        monkeypatch.setattr(
            listener.executor,
            "account_evidence",
            lambda: {
                "trade_mode": 0,
                "trade_mode_name": "demo",
                "currency": "EUR",
                "login": 7,
                "server": "demo",
            },
        )
        monkeypatch.setattr(
            listener.executor.mt5,
            "positions_get",
            lambda **_kwargs: [SimpleNamespace(ticket=701)],
        )
        sl_requests, tp_requests, monitor_starts = [], [], []
        monkeypatch.setattr(
            listener.pending_actions,
            "enqueue_modify_sl",
            lambda _sig, ticket, price, **_kwargs: sl_requests.append(
                (ticket, price)
            ),
        )
        monkeypatch.setattr(
            listener.pending_actions,
            "enqueue_modify_tp",
            lambda _sig, ticket, price, **_kwargs: tp_requests.append(
                (ticket, price)
            ),
        )

        async def monitor_start(signal):
            monitor_starts.append(signal)

        monkeypatch.setattr(listener, "_place_dca", monitor_start)
        monkeypatch.setattr(listener.journal, "event", lambda *a, **k: None)
        monkeypatch.setattr(listener.journal, "begin_trade", lambda *a, **k: None)
        monkeypatch.setattr(listener.logger, "log_signal", lambda *a, **k: None)
        monkeypatch.setattr(
            listener, "_emit_same_direction_overlap_anomaly", lambda *a, **k: None
        )
        intent = listener._Canal2EntryIntent(
            message_id=message_id,
            direction="BUY",
            parsed={"direction": "BUY"},
            raw_text="XAUUSD BUY NOW",
            entry_timestamp=observed.replace(tzinfo=None),
            telegram_timestamp=observed,
            source_kind="telegram_now",
            command_key="BUY_NOW",
        )
        record = listener._Gold555PendingEntry(
            intent=intent,
            watch=watch,
            order_started=True,
            durable_reconcile_pending=True,
        )
        assert listener._canal2_open_claim(message_id)
        listener._gold_555_entry_watches[message_id] = record

        first = await listener.process_gold_555_entry_tick(
            SimpleNamespace(bid=2500.2, ask=2500.4, time_msc=1),
            now=observed,
        )
        second = await listener.process_gold_555_entry_tick(
            SimpleNamespace(bid=2500.3, ask=2500.5, time_msc=2),
            now=observed,
        )

        signal = runtime_state.get("canal2", message_id)
        assert (first, second) == (1, 0)
        assert signal is not None and signal.market_ticket == 701
        # The order already carried 2470.40 from the confirmed quote. Recovery
        # must not loosen it to the fill-derived 2470.25.
        assert sl_requests == [(701, 2470.4)]
        assert tp_requests == [(701, 2500.75)]
        assert monitor_starts == [signal]
        assert marker.read_text(encoding="ascii").splitlines() == ["send"]
    finally:
        listener._gold_555_entry_watches.clear()
        listener._canal2_opening_msg_ids.clear()
        await client.close()
