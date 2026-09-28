"""Broker quotes advance while the actual monitor or pending queue awaits MT5."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from tests.test_runtime_replay_entry_controller import assert_path
from tests.test_runtime_replay_monitor import monitored, next_quote


@asynccontextmanager
async def busy_turn(run, *, preparation=False):
    for queue in (run.mailbox.prepared, run.mailbox.applied):
        while not queue.empty():
            queue.get_nowait()
    run.mailbox.hold_preparations = preparation
    run.mailbox.hold_responses = not preparation
    turn = asyncio.create_task(next_quote(run))
    try:
        barrier = run.mailbox.prepared if preparation else run.mailbox.applied
        checkpoint = await asyncio.wait_for(barrier.get(), 5)
        assert not turn.done()
        run.evidence["busy_checkpoint"] = checkpoint
        yield turn
    finally:
        run.mailbox.release_preparations()
        run.mailbox.release_responses()
        if not turn.done():
            turn.cancel()
        await asyncio.wait_for(asyncio.gather(turn, return_exceptions=True), 5)


def opens(run):
    return [row for row in run.mailbox.records if row["name"] == "order_send"
            and row["args"][0]["action"] == 1 and "position" not in row["args"][0]]


def complete(run, pnl, *, legs, expected_rejection=None, expected_open_deferral=False):
    assert run.task.done() and run.task.exception() is None
    assert run.signal.status == "closed" and run.signal.journal_finalized
    assert not run.owner.snapshot()["positions"]
    assert len(run.finalized) == 1
    if expected_open_deferral:
        assert expected_rejection is None and len(run.anomalies) == 1
        refusal = run.anomalies[0]
        assert refusal["kwargs"]["code"] == "finalize_blocked_mt5_positions_open"
        assert refusal["kwargs"]["phase"] == "before_finalize"
        assert refusal["kwargs"]["open_tickets"] == [1002]
        assert refusal["kwargs"]["closed_by"] == "profit_lock"
    elif expected_rejection is None:
        assert not run.anomalies
    else:
        assert len(run.anomalies) == 1
        rejection = run.anomalies[0]
        assert rejection["args"][:3] == ("canal2_98766", "mt5", "info")
        assert rejection["args"][3].startswith(expected_rejection)
        assert rejection["kwargs"]["retcode"] == 10036
        assert rejection["kwargs"]["ticket"] == 1001 and rejection["kwargs"]["attempts"] == 1
    assert run.finalized[0]["kwargs"]["total_pnl_usd"] == pytest.approx(pnl)
    result = run.owner.finish()
    assert not result.blockers
    assert len(result.entries) == len(result.exits) == legs
    assert float(result.pnl_eur) == pytest.approx(pnl)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_full_monitor_stop_during_dca_ack_keeps_closed_positions_closed(tmp_path, monkeypatch, direction):
    async with monitored(tmp_path, monkeypatch, direction,
            quotes=[100., 98.9, 100.4, 98.9, 60., 60., 60.],
            offsets=[0, 1, 2, 3, 4, 1803, 1804],
            trace_contract="runtime_gold_monitor_busy_v1") as run:
        async with busy_turn(run) as turn:
            assert len(opens(run)) == 2
            assert len(run.owner.snapshot()["positions"]) == 2
            assert run.owner.snapshot()["entries"][1]["acknowledged_ns"] is None
            assert run.signal.dca_tickets == []
            assert run.owner.advance()
            assert not run.owner.snapshot()["positions"]
            assert not run.task.done() and not turn.done()
            run.mailbox.release_responses()
            await asyncio.wait_for(turn, 5)
        assert run.signal.dca_tickets == [1002]
        assert run.owner.snapshot()["entries"][1]["acknowledged_ns"] == run.owner.snapshot()["time_ns"]
        assert run.signal.status == "open" and not run.finalized
        # Automatic flat preserves unused intents until their declared expiry.
        await next_quote(run)
        complete(run, -279.7, legs=2)
        assert len(opens(run)) == 2
        assert_path(run, [0, 0, -80, -740, -27970, -27970, -27970],
                    [0, 0, .04, .07, 0, 0, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_full_monitor_delayed_dca_prepare_uses_current_fill_and_target(tmp_path, monkeypatch, direction):
    async with monitored(tmp_path, monkeypatch, direction,
            quotes=[100., 98.9, 100.4, 98.9, 98.4, 105., 105.],
            offsets=[0, 1, 2, 3, 4, 1803, 1804],
            trace_contract="runtime_gold_monitor_busy_v1") as run:
        async with busy_turn(run, preparation=True) as turn:
            assert len(opens(run)) == 1
            assert run.owner.advance()
            assert not turn.done()
            run.mailbox.release_preparations()
            await asyncio.wait_for(turn, 5)
        fill = 98.4 if direction == "BUY" else 101.6
        target = 99.4 if direction == "BUY" else 100.6
        assert run.owner.snapshot()["entries"][1]["entry_price"] == pytest.approx(fill)
        assert run.signal.candidate_entry_prices_by_ticket[1002] == pytest.approx(fill)
        assert run.signal.tp_by_ticket[1002] == pytest.approx(target)
        await next_quote(run)
        complete(run, 5., legs=2)
        assert len(opens(run)) == 2
        assert_path(run, [0, 0, -80, -680, -940, 500, 500], [0, 0, .04, .04, .07, 0, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_full_monitor_preserves_unfilled_intents_after_native_stop_before_expiry(tmp_path, monkeypatch, direction):
    async with monitored(tmp_path, monkeypatch, direction,
            quotes=[100., 98.9, 100.4, 98.9, 60., 60., 60., 105., 105.],
            offsets=[0, 1, 2, 3, 4, 5, 6, 40, 41],
            trace_contract="runtime_gold_monitor_busy_v1") as run:
        async with busy_turn(run) as turn:
            assert run.owner.advance()
            assert not run.owner.snapshot()["positions"]
            run.mailbox.release_responses()
            await asyncio.wait_for(turn, 5)
        assert run.signal.status == "open" and not run.finalized
        # New actions added during a queue pass may need the next broker tick.
        await next_quote(run, settle_queue=False)
        assert run.signal.candidate_filled_leg_indexes == [1, 2, 3, 4]
        assert {p["ticket"] for p in run.owner.snapshot()["positions"]} == {1003, 1004, 1005}
        await next_quote(run)
        assert all(p["tp"] > 0 for p in run.owner.snapshot()["positions"])
        await next_quote(run)
        complete(run, -261.7, legs=5)
        assert len(opens(run)) == 5
        assert_path(run, [0, 0, -80, -740, -27970, -28150, -28150, -26170, -26170],
                    [0, 0, .04, .07, 0, .09, .09, 0, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("preparation", [False, True])
async def test_full_monitor_native_stop_during_trailing_never_installs_on_closed_position(
        tmp_path, monkeypatch, direction, preparation):
    async with monitored(tmp_path, monkeypatch, direction,
            quotes=[100., 98.9, 100.4, 100.8, 60., 60., 60.],
            offsets=[0, 1, 2, 3, 4, 1803, 1804],
            trace_contract="runtime_gold_monitor_busy_v1") as run:
        async with busy_turn(run, preparation=preparation) as turn:
            if not preparation:
                assert run.evidence["busy_checkpoint"]["args"][0]["action"] == 6
            assert run.owner.advance()
            assert not run.owner.snapshot()["positions"]
            run.mailbox.release_preparations()
            run.mailbox.release_responses()
            await asyncio.wait_for(turn, 5)
        await next_quote(run)
        complete(run, -162.4, legs=1, expected_rejection="MODIFY_SLTP" if preparation else None)
        assert len(opens(run)) == 1
        sends = [r for r in run.mailbox.records if r["name"] == "order_send"]
        late = [r for r in sends if r["quote_index"] >= 4]
        assert len(late) == (1 if preparation else 0)
        assert all(r["result"]["retcode"] == 10036 and not r["positions_after"] for r in late)
        assert_path(run, [0, 0, -80, 80, -16240, -16240, -16240], [0, 0, .04, .04, 0, 0, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("preparation", [False, True])
async def test_full_monitor_time_exit_wait_cannot_duplicate_a_native_exit(tmp_path, monkeypatch, direction, preparation):
    async with monitored(tmp_path, monkeypatch, direction,
            quotes=[100., 98.9, 100.4, 100.8, 60., 60., 60.],
            offsets=[0, 1, 2, 10803, 10804, 10809, 10810],
            trace_contract="runtime_gold_monitor_busy_v1") as run:
        async with busy_turn(run, preparation=preparation) as turn:
            if not preparation:
                assert run.evidence["busy_checkpoint"]["args"][0]["position"] == 1001
                assert not run.owner.snapshot()["positions"]
            assert run.owner.advance()
            assert not run.owner.snapshot()["positions"]
            run.mailbox.release_preparations()
            run.mailbox.release_responses()
            await asyncio.wait_for(turn, 5)
        if not run.task.done():
            await next_quote(run)
        else:
            assert run.owner.advance()
            assert not run.owner.snapshot()["positions"]
        result = complete(run, -162.4 if preparation else .8, legs=1,
                          expected_rejection="CLOSE_POSITION" if preparation else None)
        assert result.exits[0].reason == ("runtime_native_sl" if preparation else "runtime_market_close")
        assert run.finalized[0]["kwargs"]["closed_by"] == ("SL" if preparation else "non_negative_time_exit")
        closes = [r for r in run.mailbox.records if r["name"] == "order_send" and "position" in r["args"][0]
                  and r["args"][0]["action"] == 1]
        assert len(closes) == 1
        assert closes[0]["result"]["retcode"] == (10036 if preparation else 10009)
        assert_path(run, ([0, 0, -80, 80, -16240, -16240, -16240] if preparation else
                          [0, 0, -80, 80, 80, 80, 80]),
                    [0, 0, .04, .04 if preparation else 0, 0, 0, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_full_monitor_profit_lock_uses_realized_and_floating_money(tmp_path, monkeypatch, direction):
    # Explicit hypothetical contract permits the frozen EUR30 arm threshold;
    # this is not a claim about the production broker's contract specification.
    async with monitored(tmp_path, monkeypatch, direction, contract_size=200.,
            quotes=[100., 98.9, 100.4, 98.9, 97.4, 95.9, 94.4, 99.9, 99.7, 99.7, 99.7],
            offsets=[0, 1, 2, 3, 4, 5, 6, 7, 40, 46, 47],
            trace_contract="runtime_gold_monitor_profit_lock_v1") as run:
        for _ in range(4):
            await next_quote(run)
        await next_quote(run)
        assert run.signal.basket_guard_armed and not run.signal.basket_guard_triggered
        assert run.signal.basket_guard_peak_pl == pytest.approx(35.2)
        assert sum(run.signal.basket_guard_realized_by_ticket.values()) == pytest.approx(36.)
        assert {p["ticket"] for p in run.owner.snapshot()["positions"]} == {1001, 1002}
        await next_quote(run)
        assert run.signal.basket_guard_triggered
        if not run.task.done():
            await next_quote(run)
        else:
            assert run.owner.advance()
        result = complete(run, 32.4, legs=5, expected_open_deferral=True)
        assert sum(exit.reason == "runtime_market_close" for exit in result.exits) == 2
        assert sum(exit.reason == "runtime_native_tp" for exit in result.exits) == 3
        assert_path(run, [0, 0, -160, -1480, -3700, -6820, -10840, 3520, 3240, 3240, 3240],
                    [0, 0, .04, .07, .10, .13, .16, .07, 0, 0, 0])
