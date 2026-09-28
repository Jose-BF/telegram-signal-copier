import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import numpy as np

import config
import gold_555_live_candidate
import journal
import pending_actions
from durable_execution import DurableExecutionService
from mt5_protocol import LookupState
from mt5_worker import WorkerConfig
from state import Signal
from tests.runtime_replay_transport import InProcessMT5Client

from research.dubai_iterative.runtime_control import NativeProtectionEffect, RuntimeBrokerIdentity
from research.dubai_iterative.protection import ProtectionBlocked
from tests.runtime_replay_support import ReplayOwner, RuntimeMailbox
from tests.test_client_terminal_close import execution, strategy, tape
from tests.test_iterative_market import market
from tests.test_shared_policy_replay import spec


def model(direction="BUY", quotes=None, *, magic=111, legs=1):
    quotes = quotes or ([100.] * 3 + ([101.] * 6 if direction == "BUY" else [99.] * 6))
    rules = strategy() if legs == 1 else strategy(leg_count=legs, volume_weights=(.04, .03)[:legs],
        entry_ladder_mode="adverse", entry_ladder_step=1.5)
    item = spec("canal2_runtime", [], tape=tape(quotes, direction=direction, close_at=100),
        strategy=rules, execution=replace(execution(),
            market=market(entry_acknowledgement_delay_ms=0, close_acknowledgement_delay_ms=0)))
    owner = ReplayOwner(item, RuntimeBrokerIdentity("XAUUSD", magic, 1000))
    assert owner.advance()
    assert owner.snapshot()["entries"][0]["acknowledged_ns"] is not None
    return owner


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_external_native_effect_changes_the_original_replay_book(direction):
    owner = model(direction)
    try:
        snapshot = owner.snapshot()
        sign = 1 if direction == "BUY" else -1
        request = {"action": 6, "position": 1001, "sl": 100. - sign * 30., "tp": 100. + sign * .5}
        assert owner.apply(request)["retcode"] == 10009
        after = owner.snapshot()["positions"][0]
        assert after["sl"] == request["sl"] and after["tp"] == request["tp"]
        result = owner.finish()
        assert not result.blockers
        assert len(result.entries) == len(result.exits) == 1
        assert result.exits[0].reason == "runtime_native_tp"
        assert result.exits[0].tick_index == 3
        assert float(result.pnl_eur) == pytest.approx(2.)
        assert snapshot["positions"][0]["sl"] == snapshot["positions"][0]["tp"] == 0.
    finally:
        owner.close()


def test_external_effect_requires_current_clock_and_cannot_create_positions():
    owner = model()
    try:
        before = owner.snapshot()
        effect = NativeProtectionEffect(1001, 70., 100.5, 1, before["time_ns"])
        with pytest.raises(ProtectionBlocked, match="causal_clock"):
            owner.broker.runtime_control.apply_protection(effect)
        missing = replace(effect, quote_index=0, ticket=9000)
        assert owner.broker.runtime_control.apply_protection(missing)["retcode"] == 10036
        assert owner.snapshot() == before
    finally:
        owner.close()


@pytest.mark.parametrize("value", [-1., float("nan"), float("inf"), True])
def test_invalid_native_effect_does_not_enter_model(value):
    with pytest.raises(ValueError):
        NativeProtectionEffect(1001, value, 0., 0, 1)


@asynccontextmanager
async def runtime(tmp_path, monkeypatch, direction="BUY", quotes=None, *, legs=1):
    signal = Signal(channel="canal2", message_id=98765, direction=direction,
        timestamp=datetime.now(timezone.utc), market_ticket=1001, market_fill_price=100.,
        live_strategy_id=gold_555_live_candidate.CANDIDATE_ID,
        live_strategy_fingerprint=gold_555_live_candidate.CANDIDATE_FINGERPRINT)
    owner = model(direction, quotes, magic=signal.magic, legs=legs)
    owner.monitor_busy = True
    mailbox = RuntimeMailbox(owner)
    client = InProcessMT5Client(WorkerConfig(7, "demo", ("XAUUSD",)),
        mailbox=mailbox, store_path=tmp_path / "intents.sqlite3")
    queue = pending_actions.PendingQueue(spool_path=tmp_path / "pending.json",
                                        execution_service=DurableExecutionService(client))
    events, attempts = [], asyncio.Queue()
    monkeypatch.setattr(config, "MT5_SYMBOL", "XAUUSD")
    monkeypatch.setattr(pending_actions, "queue", queue)
    monkeypatch.setattr(journal, "event", lambda sig, ev, **fields:
                        events.append({"sig": sig, "ev": ev, **fields}))
    original = queue._try_once
    async def observed(action):
        result = await original(action)
        attempts.put_nowait(result)
        return result
    monkeypatch.setattr(queue, "_try_once", observed)
    try:
        assert (await client.start()).state is LookupState.FOUND
        yield SimpleNamespace(owner=owner, mailbox=mailbox, client=client, queue=queue,
                              signal=signal, events=events, attempts=attempts)
    finally:
        try:
            trace = runtime_trace(owner, client, mailbox, signal, queue, events)
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
        from tools.run_protection_controls import save
        save(tmp_path / "runtime-control.json", trace)


def runtime_trace(owner, client, mailbox, signal, queue, events):
    inputs = asdict(owner.spec)
    inputs["path"] = {key: value.tolist() if isinstance(value, np.ndarray) else value
                      for key, value in inputs["path"].items()}
    return {"contract": "isolated_runtime_protection_control_v1", "input": inputs,
            "runtime_identity": asdict(owner.runtime_identity), "envelopes": client.messages,
            "backend_calls": mailbox.records, "risk": owner.risk, "events": events,
            "result": asdict(owner.result) if owner.result is not None else None,
            "broker": owner.snapshot(), "confirmed_sl": dict(signal.sl_by_ticket),
            "confirmed_tp": dict(signal.tp_by_ticket), "pending_actions": queue._spool_snapshot(),
            "native_broker_verified": False, "complete_strategy_admitted": False,
            "full_live_parity_verified": False}


async def first_leg_protection(run):
    from listener import _queue_gold_555_first_leg_protection
    policy = gold_555_live_candidate.Gold555Policy()
    sl = policy.initial_stop(run.signal.direction, run.signal.market_fill_price)
    tp = policy.target_price(run.signal.direction, run.signal.market_fill_price, 0)
    await pending_actions.persist_async(_queue_gold_555_first_leg_protection,
                                       run.signal, 1001, sl, tp)
    return sl, tp


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_real_queue_worker_and_gold_protection_change_replay_path(tmp_path, monkeypatch, direction):
    async with runtime(tmp_path, monkeypatch, direction) as run:
        sl, tp = await first_leg_protection(run)
        assert await asyncio.wait_for(run.attempts.get(), 5) == "DONE"
        await asyncio.wait_for(run.queue._task, 5)
        assert not run.queue._actions
        position = run.owner.snapshot()["positions"][0]
        assert (position["sl"], position["tp"]) == (sl, tp)
        assert run.signal.sl_by_ticket == {1001: sl}
        assert run.signal.tp_by_ticket == {1001: tp}
        sends = [row for row in run.mailbox.records if row["name"] == "order_send"]
        assert len(sends) == 1
        assert sends[0]["args"][0]["position"] == 1001
        phases = [row["phase"] for row in run.client.messages if "phase" in row]
        assert phases == ["prepare", "commit"]
        result = run.owner.finish()
        assert not result.blockers
        assert result.exits[0].reason == "runtime_native_tp"
        assert result.exits[0].tick_index == 3
        assert float(result.pnl_eur) == pytest.approx(2.)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_native_exit_precedes_held_runtime_ack(tmp_path, monkeypatch, direction):
    async with runtime(tmp_path, monkeypatch, direction) as run:
        run.mailbox.hold_responses = True
        sl, tp = await first_leg_protection(run)
        applied = await asyncio.wait_for(run.mailbox.applied.get(), 5)
        assert applied["positions_after"][0]["sl"] == sl
        assert not run.signal.sl_by_ticket and not run.signal.tp_by_ticket
        for _ in range(3):
            assert run.owner.advance()
        assert not run.owner.snapshot()["positions"]
        assert not run.signal.tp_by_ticket
        run.mailbox.release_responses()
        assert await asyncio.wait_for(run.attempts.get(), 5) == "DONE"
        await asyncio.wait_for(run.queue._task, 5)
        result = run.owner.finish()
        assert not result.blockers
        assert len(result.exits) == 1 and result.exits[0].tick_index == 3
        assert result.exits[0].reason == "runtime_native_tp"
        assert not run.owner.snapshot()["positions"]


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_real_worker_defers_invalid_sl_but_installs_tp_then_preserves_it(tmp_path, monkeypatch, direction):
    sign = 1 if direction == "BUY" else -1
    quotes = [100., 100. + sign, 100. + sign * 2] + [100. + sign * 11] * 6
    async with runtime(tmp_path, monkeypatch, direction, quotes) as run:
        tp = 100. + sign * 10
        await pending_actions.persist_async(pending_actions.enqueue_modify_sltp, run.signal, 1001, 100., tp)
        assert await asyncio.wait_for(run.attempts.get(), 5) == "WAIT_PRECONDITION"
        position = run.owner.snapshot()["positions"][0]
        assert (position["sl"], position["tp"]) == (0., tp)
        assert not run.signal.sl_by_ticket and run.signal.tp_by_ticket == {1001: tp}
        assert run.queue._actions[0].new_sl == 100. and run.queue._actions[0].new_tp is None
        assert run.owner.advance()
        assert await asyncio.wait_for(run.attempts.get(), 5) == "DONE"
        await asyncio.wait_for(run.queue._task, 5)
        sends = [row["args"][0] for row in run.mailbox.records if row["name"] == "order_send"]
        assert [(row["sl"], row["tp"]) for row in sends] == [(0., tp), (100., tp)]
        result = run.owner.finish()
        assert not result.blockers
        assert result.exits[0].reason == "runtime_native_tp"
        assert float(result.pnl_eur) == pytest.approx(40.)


@pytest.mark.asyncio
async def test_new_revision_survives_old_ack_and_reaches_same_book(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch, quotes=[100.] * 9) as run:
        run.mailbox.hold_responses = True
        await first_leg_protection(run)
        await asyncio.wait_for(run.mailbox.applied.get(), 5)
        await pending_actions.persist_async(pending_actions.enqueue_modify_sl, run.signal, 1001, 90.)
        run.mailbox.release_responses()
        assert await asyncio.wait_for(run.attempts.get(), 5) == "RETRY"
        assert run.signal.sl_by_ticket == {1001: 70.}
        assert len(run.queue._actions) == 1 and run.queue._actions[0].new_sl == 90.
        assert run.owner.advance()
        assert await asyncio.wait_for(run.attempts.get(), 5) == "DONE"
        await asyncio.wait_for(run.queue._task, 5)
        assert run.signal.sl_by_ticket == {1001: 90.}
        assert run.owner.snapshot()["positions"][0]["sl"] == 90.
        sends = [row["args"][0] for row in run.mailbox.records if row["name"] == "order_send"]
        assert [(row["sl"], row["tp"]) for row in sends] == [(70., 100.5), (90., 100.5)]


@pytest.mark.asyncio
async def test_native_close_between_real_prepare_and_commit_cannot_resurrect(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch) as run:
        assert run.owner.apply({"action": 6, "position": 1001, "sl": 70., "tp": 100.5})["retcode"] == 10009
        run.mailbox.hold_preparations = True
        await pending_actions.persist_async(pending_actions.enqueue_modify_sl, run.signal, 1001, 90.)
        await asyncio.wait_for(run.mailbox.prepared.get(), 5)
        for _ in range(3):
            assert run.owner.advance()
        assert not run.owner.snapshot()["positions"]
        run.mailbox.release_preparations()
        # A native rejection is terminal DROP, unlike preflight ticket-not-found.
        assert await asyncio.wait_for(run.attempts.get(), 5) == "DROP"
        await asyncio.wait_for(run.queue._task, 5)
        sends = [row for row in run.mailbox.records if row["name"] == "order_send"]
        assert len(sends) == 1 and sends[0]["result"]["retcode"] == 10036
        assert not run.signal.sl_by_ticket
        result = run.owner.finish()
        assert not result.blockers and len(result.exits) == 1


@pytest.mark.asyncio
async def test_unknown_response_keeps_installed_protection_without_blind_resend(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch, quotes=[100.] * 9) as run:
        run.mailbox.unknown_after_effect = True
        await first_leg_protection(run)
        assert await asyncio.wait_for(run.attempts.get(), 5) == "WAIT_RECONCILIATION"
        assert run.owner.snapshot()["positions"][0]["sl"] == 70.
        assert not run.signal.sl_by_ticket
        assert run.owner.advance()
        assert await asyncio.wait_for(run.attempts.get(), 5) == "WAIT_RECONCILIATION"
        assert len([row for row in run.mailbox.records if row["name"] == "order_send"]) == 1
        assert len(run.queue._actions) == 1


@pytest.mark.asyncio
async def test_spool_failure_prevents_native_send(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch, quotes=[100.] * 9) as run:
        def full_disk(_payload):
            raise OSError("synthetic full disk")
        monkeypatch.setattr(run.queue, "_write_spool_payload", full_disk)
        with pytest.raises(OSError, match="full disk"):
            await first_leg_protection(run)
        assert await asyncio.wait_for(run.attempts.get(), 5) == "WAIT_PERSISTENCE"
        assert not [row for row in run.mailbox.records if row["name"] == "order_send"]
        assert not run.signal.sl_by_ticket
        assert run.owner.snapshot()["positions"][0]["sl"] == 0.


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_real_gold_additional_leg_protection_preserves_full_risk_path(tmp_path, monkeypatch, direction):
    import position_lifecycle_monitor as monitor
    from research.risk_metrics import money_path_metrics
    quotes = [100., 98., 98., 65.] + [65.] * 6
    if direction == "SELL":
        quotes = [200. - price for price in quotes]
    async with runtime(tmp_path, monkeypatch, direction, quotes, legs=2) as run:
        await first_leg_protection(run)
        assert await asyncio.wait_for(run.attempts.get(), 5) == "DONE"
        await asyncio.wait_for(run.queue._task, 5)
        run.owner.monitor_busy = False
        assert run.owner.advance()
        run.owner.monitor_busy = True
        second = next(p for p in run.owner.snapshot()["positions"] if p["ticket"] == 1002)
        run.signal.dca_tickets.append(1002)
        sl, tp = await pending_actions.persist_async(monitor._queue_gold_555_leg_protection,
            run.signal, ticket=1002, fill_price=second["price_open"], leg_index=1)
        assert await asyncio.wait_for(run.attempts.get(), 5) == "DONE"
        await asyncio.wait_for(run.queue._task, 5)
        installed = next(p for p in run.owner.snapshot()["positions"] if p["ticket"] == 1002)
        assert (installed["sl"], installed["tp"]) == (sl, tp)
        result = run.owner.finish()
        assert not result.blockers and len(result.entries) == len(result.exits) == 2
        assert all(row.reason == "runtime_native_sl" and row.tick_index == 3 for row in result.exits)
        # Executable side includes 0.20 spread: (-35.2 * 4) + (-33.2 * 3).
        assert float(result.pnl_eur) == pytest.approx(-240.4)
        points = [(row["tick_index"], row["realized_minor"], row["floating_minor"],
                   sum(p[1] for p in row["positions"])) for row in run.owner.risk]
        assert any(index == 0 and realized == 0 and floating == -80 and volume == .04
                   for index, realized, floating, volume in points)
        assert any(index == 1 and realized == 0 and floating == -940 and volume == .07
                   for index, realized, floating, volume in points)
        assert any(index == 3 and realized == -24040 and floating == 0 and volume == 0
                   for index, realized, floating, volume in points)
        risk = money_path_metrics([realized + floating for _, realized, floating, _ in points], origin=0)
        assert risk["max_drawdown"] == 24040 and risk["final_net"] == -24040


@pytest.mark.asyncio
@pytest.mark.parametrize("peak,drawdown", [(100., 8080), (105., 10000)])
async def test_identical_final_loss_does_not_hide_different_drawdown(tmp_path, monkeypatch, peak, drawdown):
    from research.risk_metrics import money_path_metrics
    async with runtime(tmp_path, monkeypatch, quotes=[100., peak, 80.] + [80.] * 5) as run:
        await pending_actions.persist_async(pending_actions.enqueue_modify_sltp, run.signal, 1001, 90., 110.)
        assert await asyncio.wait_for(run.attempts.get(), 5) == "DONE"
        await asyncio.wait_for(run.queue._task, 5)
        result = run.owner.finish()
        assert not result.blockers and float(result.pnl_eur) == pytest.approx(-80.8)
        values = [row["realized_minor"] + row["floating_minor"] for row in run.owner.risk]
        metrics = money_path_metrics(values, origin=0)
        assert metrics["final_net"] == -8080 and metrics["max_drawdown"] == drawdown


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_canonical_gold_trailing_changes_native_exit_without_erasing_tp(tmp_path, monkeypatch, direction):
    import position_lifecycle_monitor as monitor
    quotes = [100., 100.4, 100., 70.3] + [65.] * 5
    if direction == "SELL":
        quotes = [200. - price for price in quotes]
    async with runtime(tmp_path, monkeypatch, direction, quotes) as run:
        sl, tp = await first_leg_protection(run)
        assert await asyncio.wait_for(run.attempts.get(), 5) == "DONE"
        await asyncio.wait_for(run.queue._task, 5)
        # Caller state after a confirmed fill; the entry controller is not under test.
        run.signal.candidate_hard_stops[1001] = sl
        assert run.owner.advance()
        tick = SimpleNamespace(**run.owner.snapshot()["tick"])
        assert await monitor._apply_gold_555_trailing_stops(run.signal, tick, open_tickets={1001}) == 1
        assert await asyncio.wait_for(run.attempts.get(), 5) == "DONE"
        await asyncio.wait_for(run.queue._task, 5)
        position = run.owner.snapshot()["positions"][0]
        expected = 70.2 if direction == "BUY" else 129.8
        assert position["sl"] == expected and position["tp"] == tp
        assert run.owner.advance()
        tick = SimpleNamespace(**run.owner.snapshot()["tick"])
        assert await monitor._apply_gold_555_trailing_stops(run.signal, tick, open_tickets={1001}) == 0
        assert run.signal.candidate_hard_stops[1001] == expected
        result = run.owner.finish()
        assert not result.blockers and len(result.exits) == 1
        assert result.exits[0].tick_index == 3 and result.exits[0].reason == "runtime_native_sl"
        assert float(result.pnl_eur) == pytest.approx(-119.6)


@pytest.mark.asyncio
async def test_changed_revision_before_commit_aborts_stale_payload(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch, quotes=[100.] * 9) as run:
        run.mailbox.hold_preparations = True
        await first_leg_protection(run)
        await asyncio.wait_for(run.mailbox.prepared.get(), 5)
        await pending_actions.persist_async(pending_actions.enqueue_modify_sl, run.signal, 1001, 90.)
        run.mailbox.release_preparations()
        assert await asyncio.wait_for(run.attempts.get(), 5) == "RETRY"
        assert not [row for row in run.mailbox.records if row["name"] == "order_send"]
        assert not run.signal.sl_by_ticket
        assert run.owner.advance()
        assert await asyncio.wait_for(run.attempts.get(), 5) == "DONE"
        await asyncio.wait_for(run.queue._task, 5)
        sends = [row["args"][0] for row in run.mailbox.records if row["name"] == "order_send"]
        assert [(row["sl"], row["tp"]) for row in sends] == [(90., 100.5)]


@pytest.mark.asyncio
async def test_real_worker_rejects_position_owned_by_another_channel(tmp_path, monkeypatch):
    async with runtime(tmp_path, monkeypatch) as run:
        run.signal.channel = "canal1"
        assert run.signal.magic != run.owner.runtime_identity.magic
        await first_leg_protection(run)
        assert await asyncio.wait_for(run.attempts.get(), 5) == "DROP"
        await asyncio.wait_for(run.queue._task, 5)
        assert not [row for row in run.mailbox.records if row["name"] == "order_send"]
        assert run.owner.snapshot()["positions"][0]["sl"] == 0.
        assert not run.signal.sl_by_ticket
