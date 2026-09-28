"""Canonical cancellation must reach dispatch and late-fill handling."""

import asyncio
from types import SimpleNamespace

import pytest

import pending_actions
import position_lifecycle_monitor as monitor
from signal_lifecycle import apply_lifecycle_decision, evaluate_terminal_request
from state import StateManager
from tests.test_e3_c_second_review_monitor import durable_done
from tests.test_gold_555_monitor import _signal
from tests.test_runtime_replay_dca_controller import dca, dca_tick, held_dca, native_opens


def cancel_remaining(run):
    decision = evaluate_terminal_request(run.signal, cause="operator_close",
        open_position_count=len(run.owner.snapshot()["positions"]), observed_at=run.clock.utcnow())
    assert decision.action == "defer"
    apply_lifecycle_decision(run.signal, decision)
    assert run.signal.lifecycle_cancelled_entry_indexes == [1, 2, 3, 4]
    assert run.signal.requested_close_reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_canonical_cancel_prevents_new_dca_dispatch(tmp_path, monkeypatch, direction):
    async with dca(tmp_path, monkeypatch, direction,
            [100., 98.9, 100.4, 94.4, 105., 105.]) as run:
        cancel_remaining(run)
        assert run.owner.advance()
        before = len(run.mailbox.records)
        assert await dca_tick(run) == 0
        assert len(run.mailbox.records) == before and len(native_opens(run)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_canonical_cancel_while_preparing_aborts_native_entry(tmp_path, monkeypatch, direction):
    async with dca(tmp_path, monkeypatch, direction,
            [100., 98.9, 100.4, 98.9, 105., 105.]) as run:
        assert run.owner.advance()
        async with held_dca(run, preparation=True) as opening:
            cancel_remaining(run)
            run.mailbox.release_preparations()
            assert await asyncio.wait_for(opening, 5) == 0
        assert len(native_opens(run)) == 1
        assert run.signal.candidate_entry_reconcile_pending_indexes == []
        assert await dca_tick(run) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_late_confirmed_cancelled_leg_is_queued_for_close_not_followed_by_more_entries(
        tmp_path, monkeypatch, direction):
    async with dca(tmp_path, monkeypatch, direction,
            [100., 98.9, 100.4, 94.4, 105., 105.]) as run:
        closes = []
        # This regression asserts the command boundary, not close execution.
        monkeypatch.setattr(pending_actions, "enqueue_close_position",
                            lambda signal, ticket, **kwargs: closes.append((signal, ticket, kwargs)))
        assert run.owner.advance()
        async with held_dca(run) as opening:
            assert len(run.owner.snapshot()["positions"]) == 2
            cancel_remaining(run)
            run.mailbox.release_responses()
            assert await asyncio.wait_for(opening, 5) == 1
        assert len(closes) == 1
        assert closes[0][0] is run.signal and closes[0][1] == 1002
        assert closes[0][2]["persist_until_signal_close"] is True
        assert run.signal.candidate_filled_leg_indexes == [1]
        assert run.signal.candidate_entry_reconcile_pending_indexes == []
        assert await dca_tick(run) == 0 and len(native_opens(run)) == 2
        assert [row["args"][0]["action"] for row in run.mailbox.records if row["name"] == "order_send"] == [1, 6, 1]
        run.evidence["close_command_only_no_close_execution_claim"] = True


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_startup_recovery_of_cancelled_leg_persists_close(tmp_path, monkeypatch, direction):
    signal = _signal(direction)
    adapter, ticket, _ = durable_done(tmp_path, signal)
    apply_lifecycle_decision(signal, evaluate_terminal_request(
        signal, cause="operator_close", open_position_count=1))
    assert 1 in signal.lifecycle_cancelled_entry_indexes
    assert signal.requested_close_reason is None and not signal.basket_guard_triggered
    fresh = StateManager()
    fresh.add(signal)
    queue = pending_actions.PendingQueue(spool_path=tmp_path / "pending.json")
    monkeypatch.setattr(queue, "_ensure_runner", lambda: None)
    monkeypatch.setattr(pending_actions, "queue", queue)
    monkeypatch.setattr(monitor, "_durable_entry_executor", adapter)
    monkeypatch.setattr(monitor, "_journal_event", lambda *a, **k: None)
    monkeypatch.setattr(monitor.executor.mt5, "positions_get", lambda **kw: [SimpleNamespace(
        ticket=ticket, symbol="XAUUSD", magic=signal.magic, volume=.03,
        type=0 if direction == "BUY" else 1, sl=0., tp=0.)])
    assert await monitor.recover_durable_candidate_entries(fresh) == 1
    assert [(a.kind, a.ticket) for a in queue._actions] == [("CLOSE_POSITION", ticket)]
    assert queue._actions[0].persist_until_signal_close
    restored = pending_actions.PendingQueue(spool_path=tmp_path / "pending.json")
    monkeypatch.setattr(restored, "_ensure_runner", lambda: None)
    assert restored.restore_from_spool(fresh) == 1
    assert restored._actions[0].action_id == queue._actions[0].action_id
    assert restored._actions[0].kind == "CLOSE_POSITION"
    assert signal.candidate_entry_reconcile_pending_indexes == []
    assert ticket in signal.dca_tickets and signal.candidate_filled_leg_indexes == [1]
