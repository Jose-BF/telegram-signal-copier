"""Offline second-review controls for Gold first-entry application only."""

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import gold_555_live_candidate as gold
import listener
from durable_entry_execution import DurableEntryExecutor, EntryDispatchState
from durable_execution import DurableExecutionService
from execution_intents import IntentConflictError, IntentStore
from state import Signal, StateManager


@pytest.fixture
def recovered(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    observed = now - timedelta(seconds=10)
    message_id, ticket, fill = 384, 701, 2500.25
    payload = dict(
        symbol="XAUUSD", direction="BUY", volume=0.04, sl=2470.4,
        tp=None, loss_budget=None, protection_policy="required", deviation=30,
        magic=listener.config.magic_for("canal2"),
        comment=gold.market_comment(message_id),
    )
    ledger = tmp_path / "ledger.sqlite3"
    subprocess.run(
        [sys.executable, "-B", "-c", """
import json, sys
from execution_intents import IntentStore
from mt5_protocol import BrokerRequest, BrokerOutcome, IntentKey, IntentState
store = IntentStore(sys.argv[1])
request = BrokerRequest.create(
    IntentKey('demo/7', 'canal2', 'canal2_384', 0, 'entry-0', 'OPEN_MARKET', 0),
    json.loads(sys.argv[2]),
)
store.prepare_reserved(request, reservation_key='demo/7/canal2/canal2_384/g0/entry-0', policy_revision=0)
store.begin_dispatch(request)
store.record_outcome(request, BrokerOutcome(IntentState.DONE, retcode=10009,
    order=701, price=2500.25, filled_volume=0.04), projection_key='signal:canal2_384')
""", str(ledger), json.dumps(payload)],
        cwd=Path(__file__).resolve().parents[1], check=True,
        capture_output=True, text=True,
    )
    store = IntentStore(ledger)

    async def execute(request, **kwargs):
        return store.get(request.intent_id)

    adapter = DurableEntryExecutor(DurableExecutionService(SimpleNamespace(
        store=store, execute=execute,
        config=SimpleNamespace(expected_server="demo", expected_login=7),
    )), symbol="XAUUSD")
    intent = listener._Canal2EntryIntent(
        message_id=message_id, direction="BUY", parsed={"direction": "BUY"},
        raw_text="XAUUSD BUY NOW", entry_timestamp=observed.replace(tzinfo=None),
        telegram_timestamp=observed, source_kind="telegram_now", command_key="BUY_NOW",
    )
    watch = listener.gold_555_entry_watch.EntryWatch.new(
        "BUY", reference=2500.0, observed_at=observed, policy=gold.Gold555Policy(),
    )
    watch.status = "confirmed"
    watch.confirmed_quote = 2500.4
    watch.confirmed_at = observed
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "sig": "canal2_384", "ev": "gold_555_entry_watch_confirmed",
        "intent": listener._gold_555_intent_payload(intent), "watch": watch.to_dict(),
    }) + "\n", encoding="utf-8")

    def event(sig, ev, **fields):
        with events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(sig=sig, ev=ev, **fields), default=str) + "\n")

    async def run(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def noop(*args, **kwargs):
        pass

    listener._entry_execution_gate.reset()
    monkeypatch.setattr(listener, "state", StateManager())
    monkeypatch.setattr(listener, "_gold_555_entry_watches", {})
    monkeypatch.setattr(listener, "_canal2_opening_msg_ids", set())
    monkeypatch.setattr(listener, "_durable_entry_executor", adapter)
    monkeypatch.setattr(listener, "_run", run)
    monkeypatch.setattr(listener, "_place_dca", noop)
    monkeypatch.setattr(listener.executor, "account_evidence", lambda: {
        "trade_mode": 0, "trade_mode_name": "demo", "currency": "EUR",
        "login": 7, "server": "demo",
    })
    monkeypatch.setattr(listener.executor.mt5, "positions_get", lambda **kw: [SimpleNamespace(ticket=ticket)])
    monkeypatch.setattr(listener.config, "GOLD_555_MAX_PLANNED_LOTS_PER_SIGNAL", 0.16)
    monkeypatch.setattr(listener.journal, "event", event)
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **k: None)
    monkeypatch.setattr(listener.journal, "begin_trade", lambda *a, **k: None)
    monkeypatch.setattr(listener.logger, "log_signal", lambda *a, **k: None)
    monkeypatch.setattr(listener, "_emit_same_direction_overlap_anomaly", lambda *a, **k: None)
    monkeypatch.setattr(listener, "_log_strategy_snapshot", lambda *a, **k: None)
    sl, tp, close = [], [], []
    for name, calls in (("enqueue_modify_sl", sl), ("enqueue_modify_tp", tp), ("enqueue_close_position", close)):
        monkeypatch.setattr(listener.pending_actions, name, lambda *a, _calls=calls, **k: _calls.append(a))
    assert listener.restore_gold_555_entry_watches_from_journal(events, now=now) == 1
    yield SimpleNamespace(
        now=now, events=events, store=store, adapter=adapter, sl=sl, tp=tp, close=close,
        record=listener._gold_555_entry_watches[message_id], ticket=ticket, fill=fill,
        tick=SimpleNamespace(bid=2500.2, ask=2500.4, time_msc=1),
    )
    listener._entry_execution_gate.reset()


def existing_signal(recovered):
    signal = Signal(
        channel="canal2", message_id=384, direction="BUY",
        timestamp=recovered.now.replace(tzinfo=None), market_ticket=701,
        market_fill_price=recovered.fill,
    )
    listener.state.add(signal)
    return signal


@pytest.mark.asyncio
async def test_close_before_first_restored_tick_does_not_discard_done(recovered):
    r = recovered
    assert not r.record.order_started and r.record.durable_reconcile_pending
    assert listener._handle_gold_555_pending_management(
        384, [{"action": "CLOSE_ALL", "confidence": 1.0}], raw_text="Close now",
        source_message_id=385, tg_ts=r.now.isoformat(),
    )
    await listener.process_gold_555_entry_tick(r.tick, now=r.now)
    assert [args[1] for args in r.close] == [701], (
        "Provider CLOSE discarded a restored DONE as an unfilled watch; no close queued"
    )


@pytest.mark.asyncio
async def test_existing_signal_close_request_wins_over_protection(recovered):
    signal = existing_signal(recovered)
    signal.requested_close_reason = "PROVIDER_CLOSE"
    await listener.process_gold_555_entry_tick(recovered.tick, now=recovered.now)
    assert not recovered.sl and not recovered.tp, "Recovery enqueued new SL/TP despite pending close"
    assert [args[1] for args in recovered.close] == [701]


@pytest.mark.asyncio
async def test_existing_signal_recovery_preserves_improved_stop(recovered, monkeypatch):
    signal = existing_signal(recovered)
    improved = recovered.fill - 5.0
    signal.sl_by_ticket[recovered.ticket] = improved
    monkeypatch.setattr(
        listener.executor.mt5,
        "positions_get",
        lambda **_kwargs: [SimpleNamespace(ticket=recovered.ticket, sl=improved)],
    )

    await listener.process_gold_555_entry_tick(recovered.tick, now=recovered.now)

    assert recovered.sl[0][2] == improved
    assert signal.candidate_hard_stops[recovered.ticket] == improved


@pytest.mark.asyncio
async def test_first_entry_recovery_does_not_loosen_provisional_stop(recovered):
    existing_signal(recovered)

    await listener.process_gold_555_entry_tick(recovered.tick, now=recovered.now)

    provisional = gold.Gold555Policy().initial_stop(
        "BUY", recovered.record.watch.confirmed_quote
    )
    assert recovered.sl[0][2] == provisional


@pytest.mark.asyncio
async def test_first_entry_recovery_rejects_wrong_current_position(recovered, monkeypatch):
    monkeypatch.setattr(
        listener.executor.mt5,
        "positions_get",
        lambda **_kwargs: [SimpleNamespace(
            ticket=recovered.ticket,
            symbol="XAUUSD",
            magic=999999,
            volume=0.04,
            type=0,
        )],
    )

    assert await listener.process_gold_555_entry_tick(
        recovered.tick, now=recovered.now
    ) == 0
    assert not recovered.sl and not recovered.tp and not recovered.close
    assert 384 in listener._gold_555_entry_watches


def test_restart_preserves_close_received_while_order_in_flight(recovered):
    r = recovered
    r.record.order_started = True
    listener._handle_gold_555_pending_management(
        384, [{"action": "CLOSE_ALL", "confidence": 1.0}], raw_text="Close now",
        source_message_id=385, tg_ts=r.now.isoformat(),
    )
    assert r.record.provider_close_requested
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()
    assert listener.restore_gold_555_entry_watches_from_journal(r.events, now=r.now) == 1, (
        "In-flight CLOSE is incorrectly terminal and masks the durable open fill on restart"
    )
    assert listener._gold_555_entry_watches[384].provider_close_requested


@pytest.mark.asyncio
async def test_projection_failure_does_not_terminalize_confirmed_fill(recovered, monkeypatch):
    r = recovered
    existing_signal(r)

    def fail(*args, **kwargs):
        raise OSError("simulated spool failure")

    monkeypatch.setattr(listener.pending_actions, "enqueue_modify_tp", fail)
    with pytest.raises(OSError, match="spool failure"):
        await listener.process_gold_555_entry_tick(r.tick, now=r.now)
    durable = r.adapter.reconstruct()[0]
    assert durable.state is EntryDispatchState.CONFIRMED
    assert r.store.get(durable.intent_id).applied_utc is not None
    # Simulate a new listener process without changing the durable evidence.
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()
    restored = listener.restore_gold_555_entry_watches_from_journal(r.events, now=r.now)
    assert restored == 1, "Abort event permanently masks DONE although TP was never enqueued"


@pytest.mark.asyncio
async def test_cancelled_position_read_keeps_confirmed_application_pending(recovered, monkeypatch):
    r = recovered

    def cancelled(**kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(listener.executor.mt5, "positions_get", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await listener.process_gold_555_entry_tick(r.tick, now=r.now)
    assert 384 in listener._gold_555_entry_watches, "Cancellation orphaned DONE until process restart"


@pytest.mark.asyncio
async def test_expired_done_still_applies_to_same_signal_once(recovered):
    r = recovered
    signal = existing_signal(r)
    later = r.record.watch.expires_at + timedelta(seconds=1)
    listener._entry_execution_gate.reset()
    assert listener.restore_gold_555_entry_watches_from_journal(r.events, now=later) == 1
    assert await listener.process_gold_555_entry_tick(r.tick, now=later) == 1
    assert listener.state.get("canal2", 384) is signal
    assert len(r.sl) == len(r.tp) == 1
    assert await listener.process_gold_555_entry_tick(r.tick, now=later) == 0


@pytest.mark.asyncio
async def test_frozen_payload_reuses_levels_but_rejects_changed_semantics(recovered):
    r = recovered
    kwargs = dict(
        channel="canal2", signal_root="canal2_384", generation=0, leg="entry-0",
        revision=0, direction="BUY", volume=0.04, sl=2400.0, tp=2600.0,
        loss_budget=None, protection_policy="required", magic=listener.config.magic_for("canal2"),
        comment=gold.market_comment(384), action_id="review",
    )
    assert (await r.adapter.open_market(**kwargs)).state is EntryDispatchState.CONFIRMED
    payload = r.adapter.reconstruct()[0].payload
    assert payload["sl"] == 2470.4 and payload["tp"] is None
    for changed in ({"direction": "SELL"}, {"volume": 0.08}, {"comment": "different"}, {"loss_budget": 2.0}):
        with pytest.raises(IntentConflictError):
            await r.adapter.open_market(**(kwargs | changed))
