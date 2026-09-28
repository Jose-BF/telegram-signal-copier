"""Real Gold entry controller against an isolated modeled broker, not live MT5."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from types import SimpleNamespace
import threading
from unittest.mock import AsyncMock

import pytest

import config
import execution_intents
import listener
import pending_actions
import position_lifecycle_monitor as monitor
from durable_entry_execution import DurableEntryExecutor
from durable_execution import DurableExecutionService
from mt5_protocol import LookupState
from mt5_worker import WorkerConfig
from research.dubai_iterative.runtime_control import RuntimeBrokerIdentity
from state import StateManager
from tests.runtime_replay_support import ReplayOwner, RuntimeMailbox
from tests.runtime_replay_transport import InProcessMT5Client
from tests.test_client_terminal_close import execution, strategy, tape
from tests.test_iterative_market import market
from tests.test_shared_policy_replay import spec


class ReplayDurableExecutionService(DurableExecutionService):
    def __init__(self, client, owner):
        super().__init__(client)
        self.owner = owner

    async def reinspect_entry(self, intent_id, *, timeout=2.0):
        result = await super().reinspect_entry(intent_id, timeout=timeout)
        if result.recovered:
            snapshot = self.store.get_current_dispatch(intent_id)
            self.owner.observe_entry_reconciliation(snapshot.request, result.record.outcome)
        return result


@asynccontextmanager
async def controller(tmp_path, monkeypatch, direction="BUY", quotes=None, *,
                     trace_contract="runtime_gold_initial_entry_control_v1", offsets=None,
                     broker_history=False, contract_size=100.):
    quotes = quotes or [100., 98.9, 100.4, 100.4, 101.4, 101.4]
    if direction == "SELL":
        quotes = [200. - value for value in quotes]
    item = spec("canal2_runtime_entry", [], tape=replace(
                tape(quotes, direction=direction, offsets=offsets), contract_size=contract_size),
                strategy=strategy().with_change(provider_management_mode="ignore"),
                execution=replace(execution(), market=market(entry_acknowledgement_delay_ms=0)))
    owner = ReplayOwner(item, RuntimeBrokerIdentity("XAUUSD", config.magic_for("canal2"),
        1000, entry_owner="external_runtime",
        history_cost_profile="synthetic_zero_commission_fee_v1" if broker_history else None))
    assert owner.advance()
    mailbox = RuntimeMailbox(owner)
    client = InProcessMT5Client(WorkerConfig(7, "demo", ("XAUUSD",)), mailbox=mailbox,
                                store_path=tmp_path / "intents.sqlite3")
    service = ReplayDurableExecutionService(client, owner)
    queue = pending_actions.PendingQueue(spool_path=tmp_path / "pending.json", execution_service=service)
    events, monitor_starts, anomalies = [], [], []

    class DateTimeMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    class ReplayDateTime(datetime, metaclass=DateTimeMeta):
        @classmethod
        def now(cls, tz=None):
            now_ns = (owner.snapshot()["time_ns"] if threading.get_ident() == owner.thread
                      else mailbox.call("clock_ns"))
            value = datetime.fromtimestamp(now_ns / 1e9, timezone.utc)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return cls.now(timezone.utc).replace(tzinfo=None)

    monkeypatch.setattr(listener, "datetime", ReplayDateTime)
    monkeypatch.setattr(execution_intents, "datetime", ReplayDateTime)
    monkeypatch.setattr(listener, "state", StateManager())
    monkeypatch.setattr(listener, "_gold_555_entry_watches", {})
    monkeypatch.setattr(listener, "_canal2_opening_msg_ids", set())
    monkeypatch.setattr(listener, "_durable_entry_executor", DurableEntryExecutor(service, symbol="XAUUSD"))
    monkeypatch.setattr(config, "MT5_SYMBOL", "XAUUSD")
    monkeypatch.setattr(config, "GOLD_555_MAX_PLANNED_LOTS_PER_SIGNAL", .16)
    monkeypatch.setattr(pending_actions, "queue", queue)
    monkeypatch.setattr(listener.executor, "account_evidence", lambda: {
        "login": 7, "server": "demo", "currency": "EUR", "trade_mode": 0, "trade_mode_name": "demo"})
    monkeypatch.setattr(listener.executor, "current_tick_safe",
                        lambda: mailbox.call("symbol_info_tick", "XAUUSD"))
    monkeypatch.setattr(listener, "_shadow_register_accepted_entry", AsyncMock())
    monkeypatch.setattr(listener, "notify", AsyncMock())
    monkeypatch.setattr(listener.journal, "anomaly", lambda *args, **kwargs:
                        anomalies.append({"args": args, "kwargs": kwargs}))
    monkeypatch.setattr(listener.journal, "event", lambda sig, ev, **fields:
                        events.append({"sig": sig, "ev": ev, **fields}))
    monkeypatch.setattr(listener.journal, "begin_trade", lambda *a, **k: None)
    monkeypatch.setattr(listener.logger, "log_signal", lambda *a, **k: None)
    # The autonomous monitor is a separate integration boundary. Do not replace
    # the entry decision, durable dispatch, native effect or protection queue.
    monkeypatch.setattr(monitor, "start", lambda signal, levels: monitor_starts.append((signal, levels)))
    listener._entry_execution_gate.reset()
    base = ReplayDateTime.now(timezone.utc)
    intent = listener._Canal2EntryIntent(
        message_id=98766, direction=direction, parsed={"direction": direction},
        raw_text=f"XAUUSD {direction} NOW", entry_timestamp=base.replace(tzinfo=None),
        telegram_timestamp=base, source_kind="telegram_now", command_key=f"{direction}_NOW")
    run = SimpleNamespace(owner=owner, mailbox=mailbox, client=client, queue=queue,
                          events=events, intent=intent, clock=ReplayDateTime, monitor_starts=monitor_starts,
                          evidence={})
    try:
        assert (await client.start()).state is LookupState.FOUND
        await listener._register_gold_555_entry_watch(intent, label="runtime-control")
        yield run
    finally:
        try:
            from tools.run_protection_controls import save
            inputs = asdict(owner.spec)
            inputs["path"] = {key: value.tolist() if hasattr(value, "tolist") else value
                              for key, value in inputs["path"].items()}
            save(tmp_path / "entry-controller.json", {
                "contract": trace_contract, "input": inputs,
                "identity": asdict(owner.runtime_identity), "envelopes": client.messages,
                "backend_calls": mailbox.records, "risk": owner.risk, "events": events,
                "anomalies": anomalies,
                "broker": owner.snapshot(), "result": asdict(owner.result) if owner.result else None,
                "runtime_evidence": run.evidence,
                "full_monitor_verified": False, "native_broker_verified": False})
        finally:
            if queue._task is not None:
                queue._task.cancel()
                await asyncio.gather(queue._task, return_exceptions=True)
            mailbox.abort()
            try:
                await client.close()
            finally:
                await mailbox.close()
                owner.close()
                listener._entry_execution_gate.reset()


async def tick(run):
    return await listener.process_gold_555_entry_tick(
        SimpleNamespace(**run.owner.snapshot()["tick"]), now=run.clock.now(timezone.utc))


def assert_path(run, money, volumes):
    settled = {row["tick_index"]: row for row in run.owner.risk if row["phase"] == "settled"}
    assert list(settled) == list(range(len(money)))
    for index, (value, volume) in enumerate(zip(money, volumes, strict=True)):
        row = settled[index]
        assert row["time_ns"] == int(run.owner.spec.path.times_ns[index])
        assert row["realized_minor"] + row["floating_minor"] == value
        assert sum(position[1] for position in row["positions"]) == pytest.approx(volume)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_real_gold_watch_entry_and_protection_share_one_book(tmp_path, monkeypatch, direction):
    async with controller(tmp_path, monkeypatch, direction) as run:
        assert not run.owner.snapshot()["entries"]
        assert not [row for row in run.mailbox.records if row["name"] == "order_send"]
        assert run.owner.advance()
        assert await tick(run) == 0
        assert not run.owner.snapshot()["entries"]
        assert run.owner.advance()
        assert await tick(run) == 1
        await asyncio.wait_for(run.queue._task, 5)
        signal = listener.state.get("canal2", run.intent.message_id)
        snapshot = run.owner.snapshot()
        assert len(snapshot["entries"]) == len(snapshot["positions"]) == 1
        assert signal.market_ticket == snapshot["positions"][0]["ticket"] == 1001
        assert signal.market_fill_price == pytest.approx(100.4 if direction == "BUY" else 99.6)
        assert snapshot["entries"][0]["acknowledged_ns"] == snapshot["time_ns"]
        assert signal.sl_by_ticket[1001] == snapshot["positions"][0]["sl"]
        assert signal.tp_by_ticket[1001] == snapshot["positions"][0]["tp"]
        assert len(run.monitor_starts) == 1
        result = run.owner.finish()
        assert not result.blockers
        assert len(result.entries) == len(result.exits) == 1
        assert result.exits[0].reason == "runtime_native_tp"
        assert float(result.pnl_eur) == pytest.approx(2.)
        assert_path(run, [0, 0, -80, -80, 200], [0, 0, .04, .04, 0])
        sends = [row["args"][0] for row in run.mailbox.records if row["name"] == "order_send"]
        assert [row["action"] for row in sends] == [1, 6]
        assert "sl" in sends[0] and "tp" not in sends[0]


async def reach_confirmation(run):
    assert run.owner.advance()
    assert await tick(run) == 0
    assert run.owner.advance()


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_price_change_after_prepare_reanchors_real_signal_to_native_fill(tmp_path, monkeypatch, direction):
    async with controller(tmp_path, monkeypatch, direction,
                          [100., 98.9, 100.4, 101.4, 101.4, 102.4]) as run:
        await reach_confirmation(run)
        run.mailbox.hold_preparations = True
        opening = asyncio.create_task(tick(run))
        try:
            await asyncio.wait_for(run.mailbox.prepared.get(), 5)
            assert not run.owner.snapshot()["positions"]
            assert run.owner.advance()
            run.mailbox.release_preparations()
            assert await asyncio.wait_for(opening, 5) == 1
            await asyncio.wait_for(run.queue._task, 5)
            signal = listener.state.get("canal2", run.intent.message_id)
            fill = 101.4 if direction == "BUY" else 98.6
            assert signal.market_fill_price == signal.candidate_entry_anchor == pytest.approx(fill)
            assert run.owner.snapshot()["entries"][0]["entry_price"] == pytest.approx(fill)
            sends = [row["args"][0] for row in run.mailbox.records if row["name"] == "order_send"]
            assert sends[0]["price"] == pytest.approx(100.4 if direction == "BUY" else 99.6)
            assert signal.tp_by_ticket[1001] == pytest.approx(fill + (.5 if direction == "BUY" else -.5))
            result = run.owner.finish()
            assert not result.blockers and float(result.pnl_eur) == pytest.approx(2.)
            assert_path(run, [0, 0, 0, -80, -80, 200], [0, 0, 0, .04, .04, 0])
        finally:
            run.mailbox.release_preparations()
            await asyncio.gather(opening, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_native_stop_before_entry_response_retains_risk_and_never_reopens(tmp_path, monkeypatch, direction):
    async with controller(tmp_path, monkeypatch, direction,
                          [100., 98.9, 100.4, 65., 65., 65.]) as run:
        await reach_confirmation(run)
        run.mailbox.hold_responses = True
        opening = asyncio.create_task(tick(run))
        try:
            applied = await asyncio.wait_for(run.mailbox.applied.get(), 5)
            assert applied["args"][0]["action"] == 1
            assert run.owner.snapshot()["entries"][0]["acknowledged_ns"] is None
            assert listener.state.get("canal2", run.intent.message_id) is None
            assert run.owner.risk[-1]["floating_minor"] == -80
            assert run.owner.advance()
            assert not run.owner.snapshot()["positions"]
            assert run.owner.snapshot()["entries"][0]["acknowledged_ns"] is None
            run.mailbox.release_responses()
            assert await asyncio.wait_for(opening, 5) == 1
            await asyncio.wait_for(run.queue._task, 5)
            assert not run.owner.snapshot()["positions"]
            assert run.owner.snapshot()["entries"][0]["acknowledged_ns"] == run.owner.snapshot()["time_ns"]
            result = run.owner.finish()
            assert not result.blockers
            assert len(result.entries) == len(result.exits) == 1
            assert result.exits[0].reason == "runtime_provisional_sl"
            assert float(result.pnl_eur) == pytest.approx(-142.4)
            assert_path(run, [0, 0, -80, -14240, -14240], [0, 0, .04, 0, 0])
            sends = [row for row in run.mailbox.records if row["name"] == "order_send"]
            assert len(sends) == 1
        finally:
            run.mailbox.release_responses()
            await asyncio.gather(opening, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_unknown_entry_keeps_real_exposure_without_blind_resend(tmp_path, monkeypatch, direction):
    async with controller(tmp_path, monkeypatch, direction,
                          [100., 98.9, 100.4, 100.4, 65., 65.]) as run:
        await reach_confirmation(run)
        run.mailbox.unknown_after_effect = True
        assert await tick(run) == 0
        assert len(run.owner.snapshot()["positions"]) == 1
        assert run.owner.snapshot()["entries"][0]["acknowledged_ns"] is None
        assert listener.state.get("canal2", run.intent.message_id) is None
        assert listener._gold_555_entry_watches[run.intent.message_id].durable_reconcile_pending
        assert run.owner.advance()
        assert await tick(run) == 0
        sends = [row for row in run.mailbox.records if row["name"] == "order_send"]
        assert len(sends) == 1
        result = run.owner.finish()
        assert len(result.entries) == len(result.exits) == 1
        assert "market_lifecycle_incomplete_at_data_end" in result.blockers
        assert result.pnl_eur is None
        assert_path(run, [0, 0, -80, -80, -14240, -14240], [0, 0, .04, .04, 0, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_native_rejection_after_price_gap_does_not_register_a_signal(tmp_path, monkeypatch, direction):
    async with controller(tmp_path, monkeypatch, direction,
                          [100., 98.9, 100.4, 65., 65., 65.]) as run:
        await reach_confirmation(run)
        run.mailbox.hold_preparations = True
        opening = asyncio.create_task(tick(run))
        try:
            await asyncio.wait_for(run.mailbox.prepared.get(), 5)
            assert run.owner.advance()
            run.mailbox.release_preparations()
            assert await asyncio.wait_for(opening, 5) == 0
            assert listener.state.get("canal2", run.intent.message_id) is None
            assert not run.owner.snapshot()["positions"]
            assert run.intent.message_id not in listener._gold_555_entry_watches
            sends = [row for row in run.mailbox.records if row["name"] == "order_send"]
            assert len(sends) == 1 and sends[0]["result"]["retcode"] == 10016
            result = run.owner.finish()
            assert not result.blockers and result.unfilled and not result.entries
            assert [row.kind for row in result.market_events] == [
                "entry_requested", "entry_rejected", "entry_acknowledged"]
            assert_path(run, [0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0])
        finally:
            run.mailbox.release_preparations()
            await asyncio.gather(opening, return_exceptions=True)
