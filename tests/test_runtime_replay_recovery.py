"""Late transport DONE is not an invented reconciliation of a lost response."""

import asyncio
from types import SimpleNamespace

import pytest

import listener
import position_lifecycle_monitor as monitor
import signal_lifecycle
from durable_entry_execution import EntryDispatchState
from tests.test_late_initial_entry_recovery import closed_initial
from tests.test_runtime_replay_entry_controller import assert_path, tick
from tests.test_runtime_replay_monitor import MonitorTurns, monitored, next_quote
from tests.test_runtime_replay_monitor_concurrency import busy_turn, opens


async def wait_for_caller_timeout(run):
    async with asyncio.timeout(3):
        while not run.signal.candidate_entry_retry_failures:
            await asyncio.sleep(.001)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("startup_apply", [False, True], ids=["next_tick", "startup_application"])
async def test_caller_timeout_then_late_done_includes_already_closed_leg(
        tmp_path, monkeypatch, direction, startup_apply):
    async with monitored(tmp_path, monkeypatch, direction,
            quotes=[100., 98.9, 100.4, 98.9, 60., 60., 60.],
            offsets=[0, 1, 2, 3, 40, 1803, 1804],
            trace_contract="runtime_gold_late_done_recovery_v1") as run:
        async with busy_turn(run) as turn:
            assert len(opens(run)) == 2
            await wait_for_caller_timeout(run)
            assert run.signal.candidate_entry_reconcile_pending_indexes == [1]
            assert run.signal.dca_tickets == []
            assert run.owner.advance()
            assert not run.owner.snapshot()["positions"]
            run.mailbox.release_responses()
            await asyncio.wait_for(turn, 5)
        assert run.client.late_responses == 1
        assert run.signal.candidate_entry_reconcile_pending_indexes == [1]
        assert not run.finalized
        record = await asyncio.to_thread(listener._durable_entry_executor.lookup,
            channel=run.signal.channel, signal_root=f"{run.signal.channel}_{run.signal.message_id}",
            generation=run.signal.zone_entry_generation, leg="candidate-entry-1", revision=0)
        assert record.state is EntryDispatchState.CONFIRMED and record.ticket == 1002
        assert run.owner.snapshot()["entries"][1]["acknowledged_ns"] == run.owner.snapshot()["time_ns"]
        if startup_apply:
            assert await monitor.recover_durable_candidate_entries(listener.state) == 1
            assert run.signal.dca_tickets == [1002]
            assert run.signal.candidate_entry_reconcile_pending_indexes == []
            assert not run.queue._actions
            assert await monitor.recover_durable_candidate_entries(listener.state) == 0
        await next_quote(run)
        assert run.task.done() and run.task.exception() is None
        assert run.signal.status == "closed" and run.signal.journal_finalized
        assert len(run.finalized) == len(run.anomalies) == 1
        assert run.finalized[0]["kwargs"]["total_pnl_usd"] == pytest.approx(-279.7)
        assert run.anomalies[0]["args"][:3] == ("canal2_98766", "fill", "critical")
        assert run.anomalies[0]["kwargs"] == {
            "intent_id": record.intent_id, "retcode": None,
            "reason": "durable_dispatching", "channel": "canal2", "leg": "candidate-entry-1",
        }
        result = run.owner.finish()
        assert not result.blockers and len(result.entries) == len(result.exits) == 2
        assert float(result.pnl_eur) == pytest.approx(-279.7)
        assert len(opens(run)) == 2
        assert run.signal.candidate_filled_leg_indexes == [1]
        assert_path(run, [0, 0, -80, -740, -27970, -27970, -27970],
                    [0, 0, .04, .07, 0, 0, 0])
        run.evidence["recovery"] = {
            "path": "startup_application" if startup_apply else "next_tick",
            "caller_timeout_then_late_transport_done": True,
            "lost_response_reinspection": False,
            "whole_process_restart": False,
            "durable_outcome_revision": record.outcome_revision,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_late_initial_closed_fill_reaches_real_monitor_and_finalizer(tmp_path, monkeypatch, direction):
    original_start = monitor.start
    async with closed_initial(tmp_path, monkeypatch, direction,
            offsets=[0, 1, 2, 1803, 1840, 1841]) as run:
        turns, tasks, finalized = MonitorTurns(), [], []
        base = run.clock.now().timestamp()
        original_finalize = listener.journal.finalize_trade
        monkeypatch.setattr(monitor, "datetime", run.clock)
        monkeypatch.setattr(signal_lifecycle, "datetime", run.clock)
        monkeypatch.setattr(monitor, "time", SimpleNamespace(
            monotonic=lambda: 10000. + run.clock.now().timestamp() - base))
        monkeypatch.setattr(monitor, "asyncio", turns)
        monkeypatch.setattr(monitor, "_durable_entry_executor", listener._durable_entry_executor)

        def start(signal, levels):
            task = original_start(signal, levels)
            tasks.append(task)
            return task

        def finalize(*args, **kwargs):
            finalized.append({"args": args, "kwargs": kwargs})
            return original_finalize(*args, **kwargs)

        monkeypatch.setattr(monitor, "start", start)
        monkeypatch.setattr(listener.journal, "finalize_trade", finalize)
        try:
            assert await tick(run) == 1
            signal = listener.state.get("canal2", run.intent.message_id)
            assert len(tasks) == 1
            boundary = await turns.wait_boundary(tasks[0])
            assert boundary is not None and not finalized
            # Preserve the runtime's post-start grace before automatic finalization.
            assert run.owner.advance()
            boundary.set_result(None)
            boundary = await turns.wait_boundary(tasks[0])
            assert boundary is None and tasks[0].done() and tasks[0].exception() is None
            assert signal.status == "closed" and signal.journal_finalized
            assert len(finalized) == 1
            assert finalized[0]["kwargs"]["total_pnl_usd"] == pytest.approx(-142.4)
            assert finalized[0]["kwargs"]["closed_by"] == "SL"
            assert signal.all_filled_tickets == [1001] and not signal.dca_tickets
            assert signal.candidate_first_fill_at == run.fill_time
            assert signal.candidate_entry_expires_at == run.expiry
            assert run.queue._task is None and len(opens(run)) == 1
            result = run.owner.finish()
            assert not result.blockers and len(result.entries) == len(result.exits) == 1
            assert float(result.pnl_eur) == pytest.approx(-142.4)
            assert_path(run, [0, 0, -80, -14240, -14240, -14240], [0, 0, .04, 0, 0, 0])
            run.evidence["recovered_initial_monitor"] = {
                "finalized": finalized, "status": signal.status,
                "first_fill_at": signal.candidate_first_fill_at.isoformat(),
                "entry_expires_at": signal.candidate_entry_expires_at.isoformat(),
                "full_monitor_verified": True, "whole_process_restart": False,
                "lost_response_reinspection": False,
            }
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
