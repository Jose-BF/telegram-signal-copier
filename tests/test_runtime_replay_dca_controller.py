"""Real delayed-entry controller; autonomous monitor coverage is separate."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest

import listener
import position_lifecycle_monitor as monitor
from research.risk_metrics import money_path_metrics
from tests.test_runtime_replay_entry_controller import (
    assert_path, controller, reach_confirmation, tick,
)


@asynccontextmanager
async def dca(tmp_path, monkeypatch, direction="BUY", quotes=None):
    quotes = quotes or [100., 98.9, 100.4, 98.9, 97.4, 95.9, 94.4, 105., 105.]
    async with controller(tmp_path, monkeypatch, direction, quotes,
                          trace_contract="runtime_gold_dca_control_v1") as run:
        monkeypatch.setattr(monitor, "_durable_entry_executor", listener._durable_entry_executor)
        monkeypatch.setattr(monitor, "datetime", run.clock)
        await reach_confirmation(run)
        assert await tick(run) == 1
        await asyncio.wait_for(run.queue._task, 5)
        run.signal = listener.state.get("canal2", run.intent.message_id)
        yield run


async def dca_tick(run):
    return await monitor._process_candidate_entry_tick(
        run.signal, SimpleNamespace(**run.owner.snapshot()["tick"]), now=run.clock.utcnow())


def native_opens(run):
    return [row for row in run.mailbox.records
            if row["name"] == "order_send" and row["args"][0]["action"] == 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_all_five_real_legs_preserve_exposure_and_drawdown(tmp_path, monkeypatch, direction):
    async with dca(tmp_path, monkeypatch, direction) as run:
        for leg_index in range(1, 5):
            assert run.owner.advance()
            assert await dca_tick(run) == 1
            await asyncio.wait_for(run.queue._task, 5)
            assert run.signal.candidate_filled_leg_indexes == list(range(1, leg_index + 1))
            positions = run.owner.snapshot()["positions"]
            assert [p["ticket"] for p in positions] == list(range(1001, 1002 + leg_index))
            assert sum(p["volume"] for p in positions) == pytest.approx(.04 + .03 * leg_index)
            assert all(p["sl"] == run.signal.sl_by_ticket[p["ticket"]]
                       and p["tp"] == run.signal.tp_by_ticket[p["ticket"]] for p in positions)
        assert await dca_tick(run) == 0
        assert len(native_opens(run)) == 5
        assert [row["args"][0]["volume"] for row in native_opens(run)] == [.04, .03, .03, .03, .03]
        result = run.owner.finish()
        assert not result.blockers and len(result.entries) == len(result.exits) == 5
        assert float(result.pnl_eur) == pytest.approx(23.)
        path = [0, 0, -80, -740, -1850, -3410, -5420, 2300]
        assert_path(run, path, [0, 0, .04, .07, .10, .13, .16, 0])
        assert money_path_metrics(path, origin=0)["max_drawdown"] == 5420


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_one_gap_opens_crossed_legs_sequentially_not_twice(tmp_path, monkeypatch, direction):
    async with dca(tmp_path, monkeypatch, direction,
                   [100., 98.9, 100.4, 94.4, 94.4, 105., 105.]) as run:
        assert run.owner.advance()
        assert await dca_tick(run) == 4
        expected = 94.4 if direction == "BUY" else 105.6
        assert [e["entry_price"] for e in run.owner.snapshot()["entries"]][1:] == pytest.approx([expected] * 4)
        assert run.signal.candidate_filled_leg_indexes == [1, 2, 3, 4]
        assert await dca_tick(run) == 0
        # Actions appended during the queue's batch await its next broker tick.
        assert run.owner.advance()
        await asyncio.wait_for(run.queue._task, 5)
        assert await dca_tick(run) == 0
        assert len(native_opens(run)) == 5
        result = run.owner.finish()
        assert not result.blockers and len(result.entries) == len(result.exits) == 5
        assert float(result.pnl_eur) == pytest.approx(23.)
        assert_path(run, [0, 0, -80, -2720, -2720, 2300], [0, 0, .04, .16, .16, 0])


@asynccontextmanager
async def held_dca(run, *, preparation=False):
    # Discard notifications from the already completed initial entry/SLTP.
    for queue in (run.mailbox.prepared, run.mailbox.applied):
        while not queue.empty():
            queue.get_nowait()
    run.mailbox.hold_preparations = preparation
    run.mailbox.hold_responses = not preparation
    task = asyncio.create_task(dca_tick(run))
    try:
        barrier = run.mailbox.prepared if preparation else run.mailbox.applied
        await asyncio.wait_for(barrier.get(), 5)
        yield task
    finally:
        if not task.done():
            task.cancel()
        run.mailbox.release_preparations()
        run.mailbox.release_responses()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_delayed_dca_prepare_reanchors_exact_target_to_native_fill(tmp_path, monkeypatch, direction):
    async with dca(tmp_path, monkeypatch, direction,
                   [100., 98.9, 100.4, 98.9, 98.4, 98.4, 105., 105.]) as run:
        assert run.owner.advance()
        async with held_dca(run, preparation=True) as opening:
            assert len(native_opens(run)) == 1
            assert len(run.owner.snapshot()["entries"]) == 1
            assert run.owner.advance()
            run.mailbox.release_preparations()
            assert await asyncio.wait_for(opening, 5) == 1
        await asyncio.wait_for(run.queue._task, 5)
        expected_fill = 98.4 if direction == "BUY" else 101.6
        expected_tp = 99.4 if direction == "BUY" else 100.6
        send = native_opens(run)[1]["args"][0]
        assert send["price"] == pytest.approx(98.9 if direction == "BUY" else 101.1)
        assert "sl" in send and "tp" not in send
        entry = run.owner.snapshot()["entries"][1]
        assert entry["entry_price"] == pytest.approx(expected_fill)
        assert entry["acknowledged_ns"] == run.owner.snapshot()["time_ns"]
        assert run.signal.candidate_entry_prices_by_ticket[1002] == pytest.approx(expected_fill)
        assert run.signal.tp_by_ticket[1002] == pytest.approx(expected_tp)
        position = next(p for p in run.owner.snapshot()["positions"] if p["ticket"] == 1002)
        assert position["tp"] == pytest.approx(expected_tp)
        assert position["sl"] == run.signal.sl_by_ticket[1002]
        assert run.signal.candidate_filled_leg_indexes == [1]
        assert await dca_tick(run) == 0
        assert len(native_opens(run)) == 2
        result = run.owner.finish()
        assert not result.blockers and len(result.entries) == len(result.exits) == 2
        assert float(result.pnl_eur) == pytest.approx(5.)
        assert_path(run, [0, 0, -80, -680, -940, -940, 500], [0, 0, .04, .04, .07, .07, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_unknown_dca_effect_blocks_later_legs_and_resend_after_backoff(tmp_path, monkeypatch, direction):
    async with dca(tmp_path, monkeypatch, direction,
                   [100., 98.9, 100.4, 98.9, 94.4, 94.4, 60., 60.]) as run:
        retry_clock = [100.]
        monkeypatch.setattr(monitor, "time", SimpleNamespace(monotonic=lambda: retry_clock[0]))
        assert run.owner.advance()
        run.mailbox.unknown_after_effect = True
        assert await dca_tick(run) == 0
        assert len(run.owner.snapshot()["positions"]) == 2
        assert run.owner.snapshot()["entries"][1]["acknowledged_ns"] is None
        assert run.signal.candidate_entry_reconcile_pending_indexes == [1]
        assert run.signal.candidate_filled_leg_indexes == []
        assert run.signal.dca_tickets == []
        assert run.signal.candidate_entry_retry_not_before > retry_clock[0]
        run.mailbox.unknown_after_effect = False
        for _ in range(2):
            assert run.owner.advance()
            retry_clock[0] = run.signal.candidate_entry_retry_not_before + .01
            assert await dca_tick(run) == 0
            assert run.signal.candidate_entry_reconcile_pending_indexes == [1]
            assert run.signal.candidate_filled_leg_indexes == []
            assert len(native_opens(run)) == 2
            assert len(run.owner.snapshot()["entries"]) == 2
        result = run.owner.finish()
        assert len(result.entries) == len(result.exits) == 2
        assert "market_lifecycle_incomplete_at_data_end" in result.blockers
        assert result.pnl_eur is None
        assert_path(run, [0, 0, -80, -740, -3890, -3890, -27970, -27970],
                    [0, 0, .04, .07, .07, .07, 0, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_native_stop_before_held_dca_ack_does_not_resurrect_position(tmp_path, monkeypatch, direction):
    async with dca(tmp_path, monkeypatch, direction,
                   [100., 98.9, 100.4, 98.9, 60., 60., 60.]) as run:
        assert run.owner.advance()
        async with held_dca(run) as opening:
            assert len(run.owner.snapshot()["positions"]) == 2
            assert run.owner.snapshot()["entries"][1]["acknowledged_ns"] is None
            assert run.signal.dca_tickets == []
            assert run.owner.advance()
            assert run.owner.snapshot()["positions"] == []
            run.mailbox.release_responses()
            assert await asyncio.wait_for(opening, 5) == 1
        await asyncio.wait_for(run.queue._task, 5)
        assert run.owner.snapshot()["positions"] == []
        assert run.signal.dca_tickets == [1002]
        assert run.signal.candidate_filled_leg_indexes == [1]
        assert run.owner.snapshot()["entries"][1]["acknowledged_ns"] == run.owner.snapshot()["time_ns"]
        assert len(native_opens(run)) == 2
        sends = [row for row in run.mailbox.records if row["name"] == "order_send"]
        assert [row["args"][0]["action"] for row in sends] == [1, 6, 1]
        result = run.owner.finish()
        assert not result.blockers and len(result.entries) == len(result.exits) == 2
        assert float(result.pnl_eur) == pytest.approx(-279.7)
        assert_path(run, [0, 0, -80, -740, -27970, -27970], [0, 0, .04, .07, 0, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("invalidation", ["generation", "requested_close"])
async def test_changed_dca_intent_before_commit_aborts_without_native_extra(tmp_path, monkeypatch, direction, invalidation):
    async with dca(tmp_path, monkeypatch, direction,
                   [100., 98.9, 100.4, 98.9, 105., 105.]) as run:
        assert run.owner.advance()
        before = run.owner.snapshot()
        async with held_dca(run, preparation=True) as opening:
            if invalidation == "generation":
                run.signal.zone_entry_generation += 1
            else:
                run.signal.requested_close_reason = "CLOSE_ALL"
            run.mailbox.release_preparations()
            assert await asyncio.wait_for(opening, 5) == 0
        assert len(native_opens(run)) == 1
        assert run.owner.snapshot() == before
        assert run.signal.candidate_filled_leg_indexes == []
        assert run.signal.candidate_entry_reconcile_pending_indexes == []
        assert run.signal.dca_tickets == []
        result = run.owner.finish()
        assert not result.blockers and len(result.entries) == len(result.exits) == 1
        assert_path(run, [0, 0, -80, -680, 200], [0, 0, .04, .04, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_expired_real_dca_plan_does_not_request_another_entry(tmp_path, monkeypatch, direction):
    async with dca(tmp_path, monkeypatch, direction,
                   [100., 98.9, 100.4, 94.4, 105., 105.]) as run:
        assert run.owner.advance()
        expired_now = run.signal.candidate_entry_expires_at + timedelta(microseconds=1)
        before = run.owner.snapshot()
        record_count = len(run.mailbox.records)
        assert await monitor._process_candidate_entry_tick(
            run.signal, SimpleNamespace(**before["tick"]), now=expired_now) == 0
        assert run.signal.candidate_entry_expiry_logged
        assert run.signal.candidate_filled_leg_indexes == []
        assert run.signal.dca_tickets == []
        assert len(run.mailbox.records) == record_count
        assert run.owner.snapshot() == before
        assert len(native_opens(run)) == 1
        result = run.owner.finish()
        assert not result.blockers and len(result.entries) == len(result.exits) == 1
        assert_path(run, [0, 0, -80, -2480, 200], [0, 0, .04, .04, 0])
