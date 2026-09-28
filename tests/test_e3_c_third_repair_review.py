"""Independent counterexamples for the third E3-C repair."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import listener
import pending_actions
import position_lifecycle_monitor as monitor
from durable_entry_execution import EntryDispatchResult, EntryDispatchState
from state import Signal, StateManager
from tests.test_gold_555_monitor import _signal


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(monitor, "_journal_event", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_journal_anomaly", lambda *a, **k: None)
    monkeypatch.setattr(listener.journal, "event", lambda *a, **k: None)
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_cancelled_delayed_dispatch_remains_reconcilable_without_new_cross(
    monkeypatch,
):
    signal = _signal()
    level = monitor._candidate_entry_plan(signal)[1]["trigger_price"]
    calls = 0

    class CancelledDispatch:
        async def open_market(self, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise asyncio.CancelledError()
            return EntryDispatchResult(
                EntryDispatchState.CONFIRMED,
                "recovered-open",
                ticket=7702,
                fill_price=level,
            )

    monkeypatch.setattr(monitor, "_durable_entry_executor", CancelledDispatch())
    monkeypatch.setattr(
        monitor.executor,
        "account_evidence",
        lambda: {
            "trade_mode": 0,
            "trade_mode_name": "demo",
            "currency": "EUR",
        },
    )
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_modify_sl",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_modify_tp",
        lambda *a, **k: None,
    )
    tick = SimpleNamespace(bid=level - 0.2, ask=level - 0.1, time_msc=1)

    with pytest.raises(asyncio.CancelledError):
        await monitor._process_candidate_entry_tick(signal, tick)

    assert signal.candidate_entry_reconcile_pending_indexes == [1], (
        "A cancellation can arrive after durable dispatch; the deterministic "
        "intent must be polled again even if price no longer crosses."
    )
    away = SimpleNamespace(bid=level + 5.0, ask=level + 5.1, time_msc=2)

    assert await monitor._process_candidate_entry_tick(
        signal,
        away,
        now=signal.candidate_entry_expires_at + timedelta(seconds=1),
    ) == 1
    assert calls == 2
    assert signal.dca_tickets == [7702]
    assert signal.candidate_entry_reconcile_pending_indexes == []


@pytest.mark.asyncio
async def test_delayed_leg_rechecks_close_after_async_preparation(monkeypatch):
    signal = _signal()
    leg = monitor._candidate_entry_plan(signal)[1]
    calls = []

    def account_evidence():
        signal.requested_close_reason = "PROVIDER_CLOSE"
        return {
            "trade_mode": 0,
            "trade_mode_name": "demo",
            "currency": "EUR",
        }

    class RecordingDispatch:
        async def open_market(self, **kwargs):
            calls.append(kwargs)
            return EntryDispatchResult(
                EntryDispatchState.CONFIRMED,
                "unexpected-open",
                ticket=7702,
                fill_price=float(leg["trigger_price"]),
            )

    monkeypatch.setattr(monitor.executor, "account_evidence", account_evidence)
    monkeypatch.setattr(monitor, "_durable_entry_executor", RecordingDispatch())

    result = await monitor._open_candidate_leg(
        signal,
        leg,
        float(leg["trigger_price"]),
    )

    assert result is None
    assert calls == [], "A provider close observed before dispatch must prevent a new leg."


@pytest.mark.asyncio
async def test_delayed_leg_dispatch_guard_blocks_last_instant_close(monkeypatch):
    signal = _signal()
    leg = monitor._candidate_entry_plan(signal)[1]

    monkeypatch.setattr(
        monitor.executor,
        "account_evidence",
        lambda: {
            "trade_mode": 0,
            "trade_mode_name": "demo",
            "currency": "EUR",
        },
    )

    class GuardedDispatch:
        async def open_market(self, **kwargs):
            signal.requested_close_reason = "PROVIDER_CLOSE"
            with pytest.raises(RuntimeError, match="close requested"):
                kwargs["dispatch_guard"](SimpleNamespace())
            return EntryDispatchResult(
                EntryDispatchState.NOT_SENT,
                "guarded-open",
                reason="dispatch_guard:RuntimeError",
            )

    monkeypatch.setattr(monitor, "_durable_entry_executor", GuardedDispatch())

    assert await monitor._open_candidate_leg(
        signal,
        leg,
        float(leg["trigger_price"]),
    ) is None
    assert signal.candidate_entry_reconcile_pending_indexes == []


@pytest.mark.asyncio
async def test_dubai_recalculation_preserves_stronger_pending_stop(
    monkeypatch,
):
    signal = Signal(
        channel="canal1",
        message_id=23001,
        direction="SELL",
        market_ticket=5009,
        market_fill_price=4200.0,
    )
    listener._attach_dubai_live_candidate(
        signal,
        datetime(2026, 9, 20, 10, 0, 0),
    )
    local_queue = pending_actions.PendingQueue()
    monkeypatch.setattr(local_queue, "_ensure_runner", lambda: None)
    monkeypatch.setattr(pending_actions, "queue", local_queue)
    pending_actions.enqueue_modify_sl(
        signal,
        5009,
        4210.0,
        persist_until_signal_close=True,
    )

    async def fake_run(function, *args, **_kwargs):
        if function is listener.executor.open_position_specs:
            return {
                5009: {
                    "entry": 4200.0,
                    "volume": 0.01,
                    "symbol": "XAUUSD",
                    "sl": 0.0,
                    "point": 0.01,
                }
            }
        if function is listener.executor.basket_loss_stop_price:
            return 4228.0
        raise AssertionError(function)

    monkeypatch.setattr(listener, "_run", fake_run)

    await listener._ensure_dubai_candidate_hard_stops(signal, force=True)

    assert len(local_queue._actions) == 1
    assert local_queue._actions[0].new_sl == 4210.0, (
        "The basket recalculation must not replace an already queued stronger stop."
    )
    assert signal.candidate_hard_stops[5009] == 4210.0
    assert signal.candidate_sl_requested_levels[5009] == 4210.0


@pytest.mark.asyncio
async def test_restart_rejects_delayed_fill_outside_frozen_entry_plan(monkeypatch):
    signal = _signal()
    ticket = 7702
    record = SimpleNamespace(
        state=EntryDispatchState.CONFIRMED,
        leg="candidate-entry-1",
        ticket=ticket,
        fill_price=4298.5,
        signal_root=f"{signal.channel}_{signal.message_id}",
        channel=signal.channel,
        generation=signal.zone_entry_generation,
        policy_revision=0,
        intent_id="wrong-frozen-volume",
        payload={
            "direction": signal.direction,
            "magic": signal.magic,
            "symbol": "XAUUSD",
            "volume": 0.09,
            "protection_policy": "required",
            "comment": listener.gold_555_live_candidate.market_comment(
                signal.message_id,
                1,
            ),
        },
    )
    runtime = StateManager()
    runtime.add(signal)
    monkeypatch.setattr(
        monitor,
        "_durable_entry_executor",
        SimpleNamespace(reconstruct=lambda: [record]),
    )
    monkeypatch.setattr(
        monitor.executor.mt5,
        "positions_get",
        lambda **_kwargs: [
            SimpleNamespace(
                ticket=ticket,
                symbol="XAUUSD",
                magic=signal.magic,
                type=0,
                volume=0.09,
                sl=4268.5,
            )
        ],
    )
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_modify_sl",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_modify_tp",
        lambda *a, **k: None,
    )

    assert await monitor.recover_durable_candidate_entries(runtime) == 0
    assert ticket not in signal.all_filled_tickets
