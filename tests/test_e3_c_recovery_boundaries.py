"""Recovery must preserve entry authorization and pending protection."""

from types import SimpleNamespace
from functools import partial

import pytest

import pending_actions
import position_lifecycle_monitor as monitor
from durable_entry_execution import DurableEntryExecutor, EntryDispatchResult, EntryDispatchState
from durable_execution import DurableExecutionService
from mt5_client import MT5ReadClient
from mt5_protocol import LookupState
from mt5_worker import WorkerConfig
from state import StateManager
from tests.test_gold_555_monitor import _signal
from tests.mt5_read_fakes import FakeTradeMT5


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(monitor, "_journal_event", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_journal_anomaly", lambda *a, **k: None)
    monkeypatch.setattr(monitor.executor, "account_evidence", lambda: {
        "trade_mode": 0, "trade_mode_name": "demo", "currency": "EUR",
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_prepared_recovery_cannot_dispatch_without_price_trigger(
    monkeypatch, direction,
):
    signal = _signal(direction)
    leg = monitor._candidate_entry_plan(signal)[1]
    signal.candidate_entry_reconcile_pending_indexes = [1]
    away = leg["trigger_price"] + (5 if direction == "BUY" else -5)

    class PreparedDispatch:
        async def open_market(self, **kwargs):
            # The real transport invokes this only for an intent not yet sent.
            with pytest.raises(RuntimeError, match="price trigger"):
                kwargs["dispatch_guard"](SimpleNamespace())
            return EntryDispatchResult(EntryDispatchState.NOT_SENT, "prepared")

    monkeypatch.setattr(monitor, "_durable_entry_executor", PreparedDispatch())
    assert await monitor._open_candidate_leg(signal, leg, away) is None
    assert signal.candidate_entry_reconcile_pending_indexes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_gold_recovery_and_trailing_keep_stronger_queued_stop(
    monkeypatch, direction,
):
    signal = _signal(direction)
    local_queue = pending_actions.PendingQueue()
    monkeypatch.setattr(local_queue, "_ensure_runner", lambda: None)
    monkeypatch.setattr(pending_actions, "queue", local_queue)
    ticket = signal.market_ticket
    stronger = 4290.0 if direction == "BUY" else 4310.0
    pending_actions.enqueue_modify_sl(
        signal, ticket, stronger, persist_until_signal_close=True,
    )
    sl, _ = monitor._queue_gold_555_leg_protection(
        signal, ticket=ticket, fill_price=4300.0, leg_index=0,
    )
    assert sl == stronger
    assert signal.candidate_hard_stops[ticket] == stronger
    await monitor._apply_gold_555_trailing_stops(
        signal, SimpleNamespace(bid=4300.0, ask=4300.2, time_msc=1),
    )
    sl_actions = [a for a in local_queue._actions if a.new_sl is not None]
    assert len(sl_actions) == 1
    assert sl_actions[0].new_sl == stronger


def _recovery(monkeypatch, direction="BUY"):
    signal = _signal(direction)
    leg = monitor._candidate_entry_plan(signal)[1]
    record = SimpleNamespace(
        state=EntryDispatchState.CONFIRMED, leg="candidate-entry-1",
        ticket=7702, fill_price=leg["trigger_price"],
        signal_root=f"{signal.channel}_{signal.message_id}",
        channel=signal.channel, generation=signal.zone_entry_generation,
        policy_revision=0, intent_id="recovered-delayed-entry",
        payload={
            "direction": direction, "magic": signal.magic, "symbol": "XAUUSD",
            "volume": leg["volume"], "protection_policy": "required",
            "comment": monitor.gold_555_live_candidate.market_comment(signal.message_id, 1),
        },
    )
    runtime = StateManager()
    runtime.add(signal)
    monkeypatch.setattr(monitor, "_durable_entry_executor", SimpleNamespace(
        reconstruct=lambda: [record],
    ))
    monkeypatch.setattr(monitor.executor.mt5, "positions_get", lambda **k: [
        SimpleNamespace(
            ticket=7702, symbol="XAUUSD", magic=signal.magic,
            type=0 if direction == "BUY" else 1, volume=leg["volume"], sl=0,
        ),
    ])
    return runtime, signal, record


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("mismatch", [None, "volume", "policy_revision", "comment", "generation"])
async def test_recovery_identity_changes_one_field_only(monkeypatch, direction, mismatch):
    runtime, signal, record = _recovery(monkeypatch, direction)
    if mismatch == "volume":
        record.payload["volume"] = 0.09
    elif mismatch == "policy_revision":
        record.policy_revision = 1
    elif mismatch == "comment":
        record.payload["comment"] = "wrong"
    elif mismatch == "generation":
        record.generation += 1
    requested = []
    monkeypatch.setattr(monitor, "_queue_gold_555_leg_protection", lambda *a, **k: requested.append(k))
    assert await monitor.recover_durable_candidate_entries(runtime) == (1 if mismatch is None else 0)
    assert bool(requested) is (mismatch is None)
    assert (record.ticket in signal.dca_tickets) is (mismatch is None)


@pytest.mark.asyncio
@pytest.mark.parametrize("closing", [False, True])
async def test_restart_protection_failure_keeps_recovery_pending(monkeypatch, closing):
    runtime, signal, record = _recovery(monkeypatch)
    if closing:
        signal.requested_close_reason = "PROVIDER_CLOSE"
    calls = []

    def protection(*a, **k):
        calls.append(k)
        if len(calls) == 1:
            raise OSError("simulated spool failure")

    monkeypatch.setattr(monitor, "_queue_gold_555_leg_protection", protection)
    monkeypatch.setattr(pending_actions, "enqueue_close_position", protection)
    with pytest.raises(OSError, match="spool failure"):
        await monitor.recover_durable_candidate_entries(runtime)
    assert signal.candidate_filled_leg_indexes == []
    assert signal.dca_tickets == []
    assert signal.candidate_entry_reconcile_pending_indexes == [1]
    assert await monitor.recover_durable_candidate_entries(runtime) == 1
    assert signal.dca_tickets == [record.ticket]
    assert signal.candidate_filled_leg_indexes == [1]
    assert signal.candidate_entry_reconcile_pending_indexes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_recovered_fill_does_not_invent_stop_from_later_quote(monkeypatch, direction):
    signal = _signal(direction)
    leg = monitor._candidate_entry_plan(signal)[1]
    fill = leg["trigger_price"]
    original_sl = monitor.gold_555_live_candidate.Gold555Policy().initial_stop(direction, fill)
    signal.candidate_entry_reconcile_pending_indexes = [1]
    local_queue = pending_actions.PendingQueue()
    monkeypatch.setattr(local_queue, "_ensure_runner", lambda: None)
    monkeypatch.setattr(pending_actions, "queue", local_queue)

    class ConfirmedDispatch:
        async def open_market(self, **kwargs):
            return SimpleNamespace(
                state=EntryDispatchState.CONFIRMED, ticket=7702,
                fill_price=fill, requested_sl=original_sl,
            )

    monkeypatch.setattr(monitor, "_durable_entry_executor", ConfirmedDispatch())
    away = fill + (10 if direction == "BUY" else -10)
    assert await monitor._process_candidate_entry_tick(
        signal, SimpleNamespace(bid=away, ask=away + .2, time_msc=1),
    ) == 1
    sl_action = next(a for a in local_queue._actions if a.ticket == 7702 and a.new_sl is not None)
    assert sl_action.new_sl == original_sl


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_recovery_trigger_guard_reaches_real_transport_without_send(
    monkeypatch, tmp_path, direction,
):
    signal = _signal(direction)
    policy = monitor.gold_555_live_candidate.Gold555Policy()
    signal.candidate_entry_anchor = 2500.0
    signal.candidate_entry_legs = [
        {**leg, "trigger_price": price}
        for leg, price in zip(signal.candidate_entry_legs, policy.entry_levels(direction, 2500.0))
    ]
    leg = monitor._candidate_entry_plan(signal)[1]
    signal.candidate_entry_reconcile_pending_indexes = [1]
    marker = tmp_path / "sends.txt"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(FakeTradeMT5, marker=str(marker)),
        store_path=tmp_path / "intents.sqlite3",
    )
    results = []

    class RecordingAdapter(DurableEntryExecutor):
        async def open_market(self, **kwargs):
            result = await super().open_market(**kwargs)
            results.append(result)
            return result

    monkeypatch.setattr(monitor, "_durable_entry_executor", RecordingAdapter(
        DurableExecutionService(client), symbol="XAUUSD",
    ))
    try:
        assert (await client.start()).state is LookupState.FOUND
        away = leg["trigger_price"] + (5 if direction == "BUY" else -5)
        assert await monitor._open_candidate_leg(signal, leg, away) is None
        assert results[-1].state is EntryDispatchState.NOT_SENT
        assert results[-1].reason == "dispatch_guard:RuntimeError"
        assert not marker.exists()
        # Same identity can be sent once after a genuinely eligible quote.
        await monitor._open_candidate_leg(signal, leg, leg["trigger_price"])
        assert marker.read_text(encoding="ascii").splitlines() == ["send"]
    finally:
        await client.close()
    assert not client.is_alive
