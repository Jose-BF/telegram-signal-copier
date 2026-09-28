"""Real local Dubai lifecycle on one modeled book; no native broker certification."""

import asyncio
from collections import deque
from contextlib import asynccontextmanager
import csv
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import numpy as np

import config
import dubai_live_candidate
import execution_intents
import journal
import listener
import pending_actions
import position_lifecycle_monitor as monitor
import signal_lifecycle
import strategies
from durable_entry_execution import DurableEntryExecutor
from durable_execution import DurableExecutionService
from entry_execution_gate import EntryExecutionGate
from mt5_protocol import LookupState
from mt5_runtime import MT5Runtime, mt5
from mt5_worker import WorkerConfig
from research.dubai_iterative.runtime_control import RuntimeBrokerIdentity
from state import StateManager
from tests.runtime_replay_support import ReplayOwner, RuntimeMailbox
from tests.runtime_replay_transport import InProcessMT5Client
from tests.test_client_terminal_close import execution, strategy, tape
from tests.test_iterative_market import market
from tests.test_runtime_replay_entry_controller import assert_path
from tests.test_runtime_replay_monitor import MonitorTurns, next_quote
from tests.test_shared_policy_replay import spec
from tools.run_protection_controls import save


@asynccontextmanager
async def dubai_run(tmp_path, monkeypatch, direction, *, native_stop=False,
                    hold_initial=False, invalid_fx=False):
    declared_config = {
        "MT5_SYMBOL": "XAUUSD", "MT5_MAGIC_CANAL1": 111,
        "STRATEGY_C1_BALANCED_V1_ENABLED": True,
        "STRATEGY_C1_BASKET_GUARD_ENABLED": True,
        "STRATEGY_C1_BASKET_GUARD_POLL_S": .1,
        "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL": .09,
        "STRATEGY_C1_ENTRY_MODE": "adverse_ladder", "STRATEGY_C1_NUM_ENTRIES": 3,
        "STRATEGY_C1_ADVERSE_ACTION": "rescue_market",
    }
    for name, value in declared_config.items():
        monkeypatch.setattr(config, name, value)
    quotes = [100., 96., 100., 91.7, 91.7] if native_stop else [100., 96., 100., 99.5, 99.5, 99.5]
    offsets = [0, 1, 2, 901, 902] if native_stop else [0, 1, 2, 3, 40, 46]
    if hold_initial:
        assert not native_stop and not invalid_fx
        quotes, offsets = [100., 101., 97., 101., 100.5, 100.5, 100.5], [0, 1, 2, 3, 4, 40, 46]
    actual_quotes = quotes if direction == "BUY" else [200. - value for value in quotes]
    data = replace(tape(actual_quotes, direction=direction, offsets=offsets), contract_size=100.)
    if invalid_fx:
        data = replace(data, conversion_orientation="account_base_profit_quote",
                       fx_valid=np.zeros(len(quotes), dtype=bool))
    item = spec("canal1_runtime_dubai", [],
        tape=data,
        strategy=strategy().with_change(provider_management_mode="ignore"),
        execution=replace(execution(), market=market(entry_acknowledgement_delay_ms=0)))
    identity = RuntimeBrokerIdentity("XAUUSD", 111, 1000, entry_owner="external_runtime",
        history_cost_profile="synthetic_zero_commission_fee_v1",
        valuation_profile="linear_contract_fx_unrounded_v1")
    owner = ReplayOwner(item, identity)
    assert owner.advance()
    mailbox = RuntimeMailbox(owner)
    client = InProcessMT5Client(WorkerConfig(7, "demo", ("XAUUSD",)), mailbox=mailbox,
                                store_path=tmp_path / "intents.sqlite3")
    service = DurableExecutionService(client)
    queue = pending_actions.PendingQueue(spool_path=tmp_path / "pending.json", execution_service=service)
    entry_executor = DurableEntryExecutor(service, symbol="XAUUSD")
    runtime, previous_runtime = MT5Runtime(client), mt5.runtime
    turns, tasks, events, finalized, anomalies = MonitorTurns(), [], [], [], []
    queue_errors = []
    original_attempt = queue._try_once

    async def observe_attempt(action):
        try:
            return await original_attempt(action)
        except Exception as exc:
            queue_errors.append({"exception": type(exc).__name__, "message": str(exc),
                "action_id": action.action_id, "revision": action.revision,
                "kind": action.kind, "ticket": action.ticket,
                "new_sl": action.new_sl, "new_tp": action.new_tp})
            raise

    monkeypatch.setattr(queue, "_try_once", observe_attempt)
    original_start, original_finalize = monitor.start, journal.finalize_trade

    class DateTimeMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    class ReplayDateTime(datetime, metaclass=DateTimeMeta):
        @classmethod
        def now(cls, tz=None):
            ns = (owner.snapshot()["time_ns"] if threading.get_ident() == owner.thread
                  else mailbox.call("clock_ns"))
            value = datetime.fromtimestamp(ns / 1e9, timezone.utc)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return cls.now(timezone.utc).replace(tzinfo=None)

    base = ReplayDateTime.now().timestamp()

    class ListenerClock:
        def __getattr__(self, name):
            return getattr(time, name)

        def time(self):
            return ReplayDateTime.now(timezone.utc).timestamp()

    for module in (listener, monitor, execution_intents, signal_lifecycle, strategies):
        monkeypatch.setattr(module, "datetime", ReplayDateTime)
    monkeypatch.setattr(listener, "time", ListenerClock())
    monkeypatch.setattr(monitor, "time", SimpleNamespace(
        monotonic=lambda: 10_000 + ReplayDateTime.now().timestamp() - base))
    monkeypatch.setattr(monitor, "asyncio", turns)
    monkeypatch.setattr(listener, "state", StateManager())
    monkeypatch.setattr(listener, "_entry_execution_gate", EntryExecutionGate())
    monkeypatch.setattr(listener, "_entry_serial_locks", {})
    monkeypatch.setattr(strategies, "_recent_sl_hits", deque(maxlen=20))
    monkeypatch.setattr(listener, "_durable_entry_executor", entry_executor)
    monkeypatch.setattr(monitor, "_durable_entry_executor", entry_executor)
    monkeypatch.setattr(pending_actions, "queue", queue)
    monkeypatch.setattr(journal, "_trades", {})
    monkeypatch.setattr(journal, "_test_signals", set())
    monkeypatch.setattr(journal, "JOURNAL_FILE", tmp_path / "journal.csv")
    monkeypatch.setattr(journal, "JOURNAL_TEST_FILE", tmp_path / "journal-test.csv")
    monkeypatch.setattr(listener.executor, "account_evidence", lambda: {
        "login": 7, "server": "demo", "currency": "EUR", "trade_mode": 0, "trade_mode_name": "demo"})
    monkeypatch.setattr(listener.executor, "current_tick_safe",
                        lambda: mailbox.call("symbol_info_tick", "XAUUSD"))
    # Observational/external sinks only; admission, entries, stops and policy stay real.
    monkeypatch.setattr(listener, "compute_market_context", lambda *_: None)
    monkeypatch.setattr(listener, "_shadow_register_accepted_entry", AsyncMock())
    monkeypatch.setattr(listener, "notify", AsyncMock())
    monkeypatch.setattr(listener.logger, "log_signal", lambda *a, **k: None)
    monkeypatch.setattr(journal, "event", lambda sig, ev, **fields:
                        events.append({"sig": sig, "ev": ev, **fields}))
    monkeypatch.setattr(journal, "anomaly", lambda *a, **k: anomalies.append({"args": a, "kwargs": k}))

    def record_finalized(*args, **kwargs):
        result = original_finalize(*args, **kwargs)
        finalized.append({"args": args, "kwargs": kwargs})
        return result

    def start(signal, levels):
        task = original_start(signal, levels)
        tasks.append(task)
        return task

    monkeypatch.setattr(journal, "finalize_trade", record_finalized)
    monkeypatch.setattr(monitor, "start", start)
    msg = SimpleNamespace(id=98767, date=ReplayDateTime.now(timezone.utc),
                          text=f"{direction} GOLD NOW 99-100 TP1 105 SL 70")
    parsed = {"direction": direction, "range": (99., 100.), "tps": [105.], "sl": 70.}
    if direction == "SELL":
        parsed.update(range=(100., 101.), tps=[95.], sl=130.)
        msg.text = "SELL GOLD NOW 100-101 TP1 95 SL 130"
    run = SimpleNamespace(owner=owner, mailbox=mailbox, client=client, queue=queue,
        clock=ReplayDateTime, turns=turns, events=events, finalized=finalized, anomalies=anomalies,
        evidence={"hold_initial_prepare": hold_initial, "invalid_initial_fx": invalid_fx},
        received_at=msg.date.replace(tzinfo=None), queue_errors=queue_errors)
    opening = None
    try:
        assert (await runtime.start()).state is LookupState.FOUND
        if hold_initial:
            mailbox.hold_preparations = True
            opening = asyncio.create_task(listener._open_canal1_from_text(msg, parsed))
            prepared = await asyncio.wait_for(mailbox.prepared.get(), 5)
            assert not opening.done() and not owner.snapshot()["entries"]
            assert not [row for row in mailbox.records if row["name"] == "order_send"]
            assert prepared["native_request"]["price"] == 100.
            assert prepared["native_request"]["sl"] == pytest.approx(75. if direction == "BUY" else 125.)
            run.evidence["held_initial_preparation"] = prepared
            assert owner.advance()
            assert not owner.snapshot()["entries"] and not opening.done()
            mailbox.release_preparations()
            signal = await asyncio.wait_for(opening, 15)
        else:
            signal = await asyncio.wait_for(listener._open_canal1_from_text(msg, parsed), 15)
        if invalid_fx:
            assert signal is None and not tasks
            run.signal = None
            yield run
            return
        assert signal is not None, anomalies
        assert len(tasks) == 1
        run.signal, run.task = signal, tasks[0]
        run.boundary = await turns.wait_boundary(run.task)
        if queue._task is not None:
            await asyncio.wait_for(queue._task, 5)
        assert listener._entry_open_already_committed("canal1", msg.id)
        assert not listener._entry_open_already_committed("canal2", msg.id)
        yield run
    finally:
        try:
            inputs = asdict(owner.spec)
            inputs["path"] = {key: value.tolist() if hasattr(value, "tolist") else value
                              for key, value in inputs["path"].items()}
            signal = getattr(run, "signal", None)
            root = Path(__file__).resolve().parents[1]
            sources = {}
            for module in tuple(sys.modules.values()):
                source = getattr(module, "__file__", None)
                if source and str(source).endswith(".py"):
                    source = Path(source).resolve()
                    if source.is_relative_to(root):
                        sources[source.relative_to(root).as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
            save(tmp_path / "dubai.json", {
                "contract": "runtime_dubai_monitor_control_v1", "input": inputs,
                "configuration": declared_config, "message": vars(msg), "parsed": parsed,
                "policy": dubai_live_candidate.DubaiLivePolicy().research_payload(),
                "policy_fingerprint": dubai_live_candidate.DubaiLivePolicy().fingerprint,
                "identity": asdict(identity), "envelopes": client.messages,
                "backend_calls": mailbox.records, "risk": owner.risk,
                "broker": owner.snapshot(), "events": events, "anomalies": anomalies,
                "queue_exceptions": queue_errors,
                "runtime_finalized": finalized,
                "runtime_signal": {key: value for key, value in asdict(signal).items()
                                   if key != "finalization_lock"} if signal else None,
                "history": {str(ticket): owner.broker.runtime_control.history(ticket)
                            for ticket in signal.all_filled_tickets} if signal else {},
                "result": asdict(owner.result) if owner.result else None,
                "monitor_done_before_cleanup": bool(tasks) and all(task.done() for task in tasks),
                "loaded_source_hashes_at_export": sources, "python_version": sys.version,
                "evidence": run.evidence, "native_broker_verified": False,
                "limitations": ["synthetic_quote_and_policy_clock", "in_process_transport",
                    "linear_contract_fx_unrounded_v1", "synthetic_zero_commission_fee_v1",
                    "parsed_message_input_not_telegram_ingestion", "observational_M5_omitted",
                    "cleanup_not_atomic_checkpoint", "not_full_strategy_certification"],
            })
        finally:
            mailbox.release_preparations()
            if opening is not None and not opening.done():
                opening.cancel()
                await asyncio.wait_for(asyncio.gather(opening, return_exceptions=True), 5)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
            if queue._task is not None:
                queue._task.cancel()
                await asyncio.wait_for(asyncio.gather(queue._task, return_exceptions=True), 5)
            mailbox.abort()
            try:
                await client.close()
            finally:
                await mailbox.close()
                owner.close()
                mt5.install(previous_runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("native_stop,hold_initial", [(False, False), (True, False), (False, True)],
                         ids=["profit_lock", "native_sl", "held_prepare"])
async def test_real_dubai_entry_dca_stop_guard_history_and_finalizer(
        tmp_path, monkeypatch, direction, native_stop, hold_initial):
    async with dubai_run(tmp_path, monkeypatch, direction, native_stop=native_stop, hold_initial=hold_initial) as run:
        shift = int(hold_initial)
        sign = 1 if direction == "BUY" else -1
        first_fill = 100. + sign * shift
        assert run.signal.live_strategy_id == dubai_live_candidate.CANDIDATE_ID
        assert run.signal.live_strategy_fingerprint == dubai_live_candidate.CANDIDATE_FINGERPRINT
        assert [p["volume"] for p in run.owner.snapshot()["positions"]] == [.01]
        assert run.owner.snapshot()["positions"][0]["sl"] == pytest.approx(first_fill - sign * 25.)
        assert run.signal.market_fill_price == run.signal.candidate_entry_anchor == first_fill
        assert run.signal.candidate_first_fill_at == run.received_at + timedelta(seconds=shift)
        assert run.signal.candidate_entry_expires_at == run.received_at + timedelta(minutes=15)
        assert [leg["trigger_price"] for leg in run.signal.candidate_entry_legs] == [
            None, first_fill - sign * 4., first_fill - sign * 8.]
        if hold_initial:
            assert run.signal.candidate_entry_expires_at != run.signal.candidate_first_fill_at + timedelta(minutes=15)
            first_entry = run.owner.snapshot()["entries"][0]
            assert first_entry["tick_index"] == 1 and first_entry["entry_price"] == first_fill
        assert run.signal.dca_placed and not run.signal.dca_tickets
        try:
            await next_quote(run)
        except TimeoutError:
            assert not run.queue_errors, run.queue_errors
            raise
        assert not run.queue_errors
        assert run.signal.candidate_filled_leg_indexes == [1]
        assert run.signal.dca_tickets == [1002]
        assert [p["volume"] for p in run.owner.snapshot()["positions"]] == [.01, .04]
        await next_quote(run)
        assert run.signal.basket_guard_armed and not run.signal.basket_guard_triggered
        assert run.signal.basket_guard_peak_pl == pytest.approx(15.)
        common_stop = first_fill - sign * 8.2
        assert [p["sl"] for p in run.owner.snapshot()["positions"]] == pytest.approx([common_stop] * 2)
        assert all(p["tp"] == 0 for p in run.owner.snapshot()["positions"])
        await next_quote(run)
        for _ in range(2):
            if run.task.done():
                break
            await next_quote(run)
        assert run.task.done() and run.task.exception() is None
        assert run.signal.status == "closed" and run.signal.journal_finalized
        assert not run.owner.snapshot()["positions"]
        assert len(run.finalized) == 1
        if native_stop:
            assert not run.anomalies
        else:
            pending_stops = [row for row in run.anomalies if row["args"][1] == "sl_be"]
            assert len(pending_stops) == int(hold_initial)
            for anomaly in pending_stops:
                # The second stop is queued after a prior post-fill request.
                # Its installation is independently asserted on both positions above.
                assert anomaly["args"] == (
                    "canal1_98767", "sl_be", "critical",
                    "Dubai broker basket SL is not yet confirmed; persistent retry active")
                assert anomaly["kwargs"] == {
                    "unresolved_tickets": [1001],
                    "strategy_id": dubai_live_candidate.CANDIDATE_ID,
                    "basket_loss_budget_eur": 25.,
                    "close_on_install_failure": False,
                }
            other_anomalies = [row for row in run.anomalies if row["args"][1] != "sl_be"]
            # Finalization may observe the first close before the second ACK.
            assert len(other_anomalies) <= 1
            for anomaly in other_anomalies:
                assert anomaly["kwargs"]["code"] == "finalize_blocked_mt5_positions_open"
                assert anomaly["kwargs"]["open_tickets"] == [1002]
                assert anomaly["kwargs"]["phase"] == "before_finalize"
        pnl = -26.5 if native_stop else 12.5
        final = run.finalized[0]["kwargs"]
        assert final["total_pnl_usd"] == pytest.approx(pnl)
        assert final["account_currency"] == "EUR"
        assert final["closed_by"] == ("SL" if native_stop else "profit_lock")
        with (tmp_path / "journal.csv").open(encoding="utf-8-sig", newline="") as stream:
            ledger = list(csv.DictReader(stream))
        assert len(ledger) == 1
        assert ledger[0]["signal_id"] == "canal1_98767"
        assert ledger[0]["closed_by"] == final["closed_by"]
        assert float(ledger[0]["total_pnl_usd"]) == pytest.approx(pnl)
        run.evidence["persisted_journal_rows"] = ledger
        result = run.owner.finish()
        assert not result.blockers
        assert len(result.entries) == len(result.exits) == 2
        assert float(result.pnl_eur) == pytest.approx(pnl)
        assert [exit.reason for exit in result.exits] == (["runtime_native_sl", "runtime_provisional_sl"]
                                                        if native_stop else ["runtime_market_close"] * 2)
        money = [-20, -500, 1500, -2650, -2650] if native_stop else [-20, -500, 1500, 1250, 1250, 1250]
        volumes = [.01, .05, .05, 0, 0] if native_stop else [.01, .05, .05, 0, 0, 0]
        assert_path(run, [0] * shift + money, [0] * shift + volumes)
        grid = {row["tick_index"]: row for row in run.owner.risk if row["phase"] == "settled"}
        expected_grid = []
        for index, row in grid.items():
            local = index - shift
            realized, floating = ((0, 0) if local < 0 else
                (0, [-20, -500, 1500][local]) if local < 3 else (round(pnl * 100), 0))
            positions = [] if local < 0 or local >= 3 else [("runtime_1", .01, first_fill)]
            if local in (1, 2):
                positions.append(("runtime_2", .04, first_fill - sign * 4.))
            assert (row["realized_minor"], row["floating_minor"]) == (realized, floating)
            assert row["positions"] == tuple(positions)
            expected_grid.append({"tick_index": index, "time_ns": int(run.owner.spec.path.times_ns[index]),
                                  "realized_minor": realized, "floating_minor": floating,
                                  "positions": positions})
        sends = [row for row in run.mailbox.records if row["name"] == "order_send"]
        opens = [row for row in sends if row["args"][0]["action"] == 1 and "position" not in row["args"][0]]
        closes = [row for row in sends if row["args"][0]["action"] == 1 and "position" in row["args"][0]]
        assert len(opens) == 2 and all(row["result"]["retcode"] == 10009 for row in opens)
        assert len(closes) == (0 if native_stop else 2)
        assert all(row["result"]["retcode"] == 10009 for row in sends)
        assert [row["quote_index"] for row in opens] == [shift, 1 + shift]
        assert [row["args"][0]["sl"] for row in opens] == pytest.approx(
            [75. if direction == "BUY" else 125., common_stop])
        assert any(row["name"] == "order_calc_profit" for row in run.mailbox.records)
        assert any(row["name"] == "history_deals_get" for row in run.mailbox.records)
        for ticket in (1001, 1002):
            history = run.owner.broker.runtime_control.history(ticket)
            assert [row["entry"] for row in history] == [0, 1]
            assert history[-1]["reason"] == (4 if native_stop else 3)
        run.evidence["scenario_assertions_completed"] = True
        run.evidence["expected_post_event_grid"] = expected_grid


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_real_dubai_required_initial_protection_rejects_unknown_fx(tmp_path, monkeypatch, direction):
    async with dubai_run(tmp_path, monkeypatch, direction, invalid_fx=True) as run:
        assert run.signal is None and listener.state.get("canal1", 98767) is None
        assert not run.owner.snapshot()["positions"] and not run.owner.snapshot()["entries"]
        assert not run.finalized and not (tmp_path / "journal.csv").exists()
        assert not [row for row in run.mailbox.records if row["name"] == "order_send"]
        assert run.owner.broker.runtime_control.calc_profit(0, "XAUUSD", .01, 100., 75.) is None
        assert run.owner.broker.runtime_control.history is not None
        valuations = [row for row in run.mailbox.records if row["name"] == "order_calc_profit"]
        assert valuations and all(row["quote_index"] == 0 for row in valuations)
        failures = [row for row in run.events if row["ev"] == "market_fill_failed"]
        assert len(failures) == 1
        assert failures[0]["dispatch_state"] == "NOT_SENT"
        assert failures[0]["reason"] == "required_protection_unavailable"
        assert not [row for row in run.client.messages if row.get("phase") == "commit"]
        initial = next(row["request"] for row in run.client.messages if row.get("phase") == "prepare")
        assert initial["payload"]["protection_policy"] == "required"
        assert initial["payload"]["loss_budget"] == 25.
        assert not listener._entry_open_in_progress("canal1", 98767)
        assert not listener._entry_open_already_committed("canal1", 98767)
        result = run.owner.finish()
        assert not result.entries and not result.exits
        assert_path(run, [0] * 6, [0] * 6)
        run.evidence["scenario_assertions_completed"] = True
        run.evidence["initial_protection_failed_without_native_order"] = True
