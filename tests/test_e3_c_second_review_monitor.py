"""Counterexamples for second E3-C repair: management must survive recovery."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import gold_555_live_candidate as gold
import listener
import position_lifecycle_monitor as monitor
from durable_entry_execution import DurableEntryExecutor, EntryDispatchResult, EntryDispatchState
from durable_execution import DurableExecutionService
from execution_intents import IntentStore
from mt5_protocol import BrokerOutcome, BrokerRequest, IntentKey, IntentState
from state import StateManager
from tests.test_dubai_live_ladder import _candidate_signal
from tests.test_gold_555_monitor import _signal


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(monitor, "_journal_event", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_journal_anomaly", lambda *a, **k: None)
    monkeypatch.setattr(monitor.executor, "account_evidence", lambda: {
        "trade_mode": 0, "trade_mode_name": "demo", "currency": "EUR",
    })
    monkeypatch.setattr(monitor.pending_actions, "enqueue_modify_sl", lambda *a, **k: None)
    monkeypatch.setattr(monitor.pending_actions, "enqueue_modify_tp", lambda *a, **k: None)


def durable_done(tmp_path, signal, *, account="demo/7"):
    policy = gold.Gold555Policy()
    fill = policy.entry_levels(signal.direction, signal.market_fill_price)[1]
    ticket = 7702
    payload = {
        "symbol": "XAUUSD", "direction": signal.direction, "volume": 0.03,
        "magic": signal.magic, "sl": policy.initial_stop(signal.direction, fill),
        "tp": None, "loss_budget": None, "protection_policy": "required",
        "comment": gold.market_comment(signal.message_id, 1), "deviation": 30,
    }
    key = IntentKey(account, signal.channel, f"{signal.channel}_{signal.message_id}",
                    0, "candidate-entry-1", "OPEN_MARKET", 0)
    request = BrokerRequest.create(key, payload)
    store = IntentStore(tmp_path / "review.sqlite3")
    store.prepare_reserved(request, reservation_key=f"{account}/review", policy_revision=0)
    store.begin_dispatch(request)
    store.record_outcome(request, BrokerOutcome(
        IntentState.DONE, retcode=10009, order=ticket, price=fill, filled_volume=0.03,
    ), projection_key=f"signal:{signal.channel}_{signal.message_id}")
    client = SimpleNamespace(store=store, config=SimpleNamespace(
        expected_login=7, expected_server="demo",
    ))
    return DurableEntryExecutor(DurableExecutionService(client), symbol="XAUUSD"), ticket, fill


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_restart_preserves_already_improved_stop(tmp_path, monkeypatch, direction):
    signal = _signal(direction)
    adapter, ticket, fill = durable_done(tmp_path, signal)
    improved = fill - 5 if direction == "BUY" else fill + 5
    signal.dca_tickets = [ticket]
    signal.candidate_filled_leg_indexes = [1]
    signal.candidate_hard_stops[ticket] = improved
    signal.sl_by_ticket[ticket] = improved
    fresh = StateManager()
    fresh.add(signal)
    monkeypatch.setattr(monitor, "_durable_entry_executor", adapter)
    monkeypatch.setattr(monitor.executor.mt5, "positions_get", lambda **kw: [SimpleNamespace(
        ticket=ticket, sl=improved, tp=0.0, magic=signal.magic, symbol="XAUUSD",
        volume=0.03, type=0 if direction == "BUY" else 1,
    )])
    requested = []
    monkeypatch.setattr(monitor.pending_actions, "enqueue_modify_sl",
                        lambda s, t, p, **k: requested.append(p))
    await monitor.recover_durable_candidate_entries(fresh)
    assert all(p >= improved if direction == "BUY" else p <= improved for p in requested), requested
    assert signal.candidate_hard_stops[ticket] == improved


@pytest.mark.asyncio
async def test_restart_does_not_apply_entry_from_another_account(tmp_path, monkeypatch):
    signal = _signal()
    adapter, ticket, fill = durable_done(tmp_path, signal, account="other-server/99")
    fresh = StateManager()
    fresh.add(signal)
    monkeypatch.setattr(monitor, "_durable_entry_executor", adapter)
    monkeypatch.setattr(monitor.executor.mt5, "positions_get", lambda **kw: [SimpleNamespace(
        ticket=ticket, symbol="XAUUSD", magic=signal.magic, sl=0.0, tp=0.0,
        price_open=fill, volume=0.03, type=0,
    )])
    requested = []
    monkeypatch.setattr(monitor.pending_actions, "enqueue_modify_tp",
                        lambda s, t, p, **k: requested.append((t, p)))
    await monitor.recover_durable_candidate_entries(fresh)
    assert requested == [], requested
    assert ticket not in signal.all_filled_tickets


@pytest.mark.asyncio
async def test_late_gold_leg_retries_incomplete_protection(monkeypatch):
    signal = _signal()
    now = datetime.utcnow()
    level = monitor._candidate_entry_plan(signal)[1]["trigger_price"]
    ticket = 7702

    async def completed(**kwargs):
        return EntryDispatchResult(EntryDispatchState.CONFIRMED, "done", ticket=ticket, fill_price=level)

    monkeypatch.setattr(monitor, "_durable_entry_executor", SimpleNamespace(open_market=completed))
    attempts, installed = [], []

    def flaky_tp(s, t, p, **kw):
        attempts.append(t)
        if len(attempts) == 1:
            raise OSError("simulated transient protection spool failure")
        installed.append(t)

    monkeypatch.setattr(monitor.pending_actions, "enqueue_modify_tp", flaky_tp)
    tick = SimpleNamespace(bid=level - 0.2, ask=level - 0.1, time_msc=1)
    signal.candidate_entry_reconcile_pending_indexes = [1]
    with pytest.raises(OSError, match="spool failure"):
        await monitor._process_candidate_entry_tick(signal, tick, now=now)
    signal.candidate_entry_expires_at = now - timedelta(seconds=1)
    await monitor._process_candidate_entry_tick(signal, tick, now=now)
    assert installed == [ticket], (attempts, signal.candidate_filled_leg_indexes)


@pytest.mark.asyncio
async def test_gold_leg_fill_does_not_loosen_provisional_stop(monkeypatch):
    signal = _signal("BUY")
    level = monitor._candidate_entry_plan(signal)[1]["trigger_price"]
    fill = level - 1.0

    async def completed(*_args, **_kwargs):
        return 7702, fill

    monkeypatch.setattr(monitor, "_open_candidate_leg", completed)
    requested = []
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_modify_sl",
        lambda _signal, _ticket, price, **_kwargs: requested.append(price),
    )
    tick = SimpleNamespace(bid=level - 0.2, ask=level - 0.1, time_msc=1)

    await monitor._process_candidate_entry_tick(signal, tick)

    assert requested == [
        gold.Gold555Policy().initial_stop("BUY", level - 0.1)
    ]


@pytest.mark.asyncio
async def test_late_dubai_leg_retries_incomplete_basket_protection(monkeypatch):
    signal, observed_at = _candidate_signal("BUY")
    ticket, fill = 7701, 4195.8
    monkeypatch.setattr(monitor, "_durable_entry_executor", SimpleNamespace())

    async def completed(*_args, **_kwargs):
        return ticket, fill

    monkeypatch.setattr(monitor, "_open_candidate_leg", completed)
    attempts, installed = [], []

    async def flaky_stops(_signal, **_kwargs):
        attempts.append(ticket)
        if len(attempts) == 1:
            raise OSError("simulated Dubai basket protection failure")
        installed.append(ticket)

    monkeypatch.setattr(listener, "_ensure_dubai_candidate_hard_stops", flaky_stops)
    tick = SimpleNamespace(bid=4195.7, ask=4195.9, time_msc=1)
    with pytest.raises(OSError, match="basket protection failure"):
        await monitor._process_candidate_entry_tick(
            signal, tick, now=observed_at + timedelta(minutes=1)
        )
    signal.candidate_entry_expires_at = observed_at
    await monitor._process_candidate_entry_tick(
        signal, tick, now=observed_at + timedelta(minutes=2)
    )
    assert installed == [ticket]
    assert signal.dca_tickets == [ticket]
    assert signal.candidate_filled_leg_indexes == [1]


@pytest.mark.asyncio
async def test_provider_close_also_closes_late_leg(monkeypatch):
    signal = _signal()
    signal.requested_close_reason = "PROVIDER_CLOSE"
    signal.candidate_entry_reconcile_pending_indexes = [1]
    level = monitor._candidate_entry_plan(signal)[1]["trigger_price"]
    ticket = 7702

    async def completed(**kwargs):
        return EntryDispatchResult(EntryDispatchState.CONFIRMED, "done", ticket=ticket, fill_price=level)

    monkeypatch.setattr(monitor, "_durable_entry_executor", SimpleNamespace(open_market=completed))
    closes = []
    monkeypatch.setattr(monitor.pending_actions, "enqueue_close_position",
                        lambda s, t, **k: closes.append(t))
    tick = SimpleNamespace(bid=level + 0.4, ask=level + 0.5, time_msc=1)
    await monitor._process_candidate_entry_tick(signal, tick)
    assert closes == [ticket], closes
