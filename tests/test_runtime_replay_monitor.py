"""The actual Gold555 monitor loop, with a controlled observation clock."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import listener
import position_lifecycle_monitor as monitor
import signal_lifecycle
from mt5_protocol import LookupState
from mt5_runtime import MT5Runtime, mt5
from tests.test_runtime_replay_entry_controller import controller, reach_confirmation, tick, assert_path


class MonitorTurns:
    """Pause only at the monitor's existing idle boundary, not inside policy."""

    def __init__(self):
        self.boundaries = asyncio.Queue()
        self.waiters = []

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, delay):
        if delay != .01:
            return await asyncio.sleep(delay)
        future = asyncio.get_running_loop().create_future()
        self.waiters.append(future)
        self.boundaries.put_nowait(future)
        await future

    async def wait_boundary(self, task):
        boundary = asyncio.create_task(self.boundaries.get())
        try:
            done, _ = await asyncio.wait({boundary, task}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
            assert done, "monitor neither completed nor reached its idle boundary"
            if task in done:
                await task
                return None
            return boundary.result()
        finally:
            if not boundary.done():
                boundary.cancel()
            await asyncio.gather(boundary, return_exceptions=True)


@asynccontextmanager
async def monitored(tmp_path, monkeypatch, direction="BUY", *, stop=False, time_exit=False,
                    quotes=None, offsets=None, trace_contract="runtime_gold_monitor_control_v1",
                    contract_size=100.):
    original_start = monitor.start
    if quotes is None:
        quotes = ([100., 98.9, 100.4, 100.8, 100.8, 100.8] if time_exit else
                  [100., 98.9, 100.4, 100.8, 98.9, 97.4, 95.9, 94.4, 60. if stop else 105., 105.])
    if offsets is None:
        offsets = [0, 1, 2, 10803, 10808, 10809] if time_exit else [0, 1, 2, 3, 4, 5, 6, 7, 40, 41]
    async with controller(tmp_path, monkeypatch, direction, quotes,
            offsets=offsets, broker_history=True, contract_size=contract_size,
            trace_contract=trace_contract) as run:
        runtime = MT5Runtime(run.client)
        previous_runtime = mt5.runtime
        turns = MonitorTurns()
        tasks, finalized, anomalies = [], [], []
        base = run.clock.now().timestamp()
        monkeypatch.setattr(monitor, "datetime", run.clock)
        monkeypatch.setattr(signal_lifecycle, "datetime", run.clock)
        monkeypatch.setattr(monitor, "time", SimpleNamespace(monotonic=lambda: 10_000 + run.clock.now().timestamp() - base))
        monkeypatch.setattr(monitor, "asyncio", turns)
        monkeypatch.setattr(monitor, "_durable_entry_executor", listener._durable_entry_executor)
        original_finalize = listener.journal.finalize_trade

        def record_finalized(*args, **kwargs):
            finalized.append({"args": args, "kwargs": kwargs})
            return original_finalize(*args, **kwargs)

        monkeypatch.setattr(listener.journal, "finalize_trade", record_finalized)
        monkeypatch.setattr(listener.journal, "anomaly", lambda *args, **kwargs:
                            anomalies.append({"args": args, "kwargs": kwargs}))

        def start(signal, levels):
            task = original_start(signal, levels)
            tasks.append(task)
            return task

        monkeypatch.setattr(monitor, "start", start)
        try:
            assert (await runtime.start()).state is LookupState.FOUND
            await reach_confirmation(run)
            assert await tick(run) == 1
            assert len(tasks) == 1
            run.signal = listener.state.get("canal2", run.intent.message_id)
            run.task, run.turns, run.finalized, run.anomalies = tasks[0], turns, finalized, anomalies
            run.boundary = await turns.wait_boundary(run.task)
            await asyncio.wait_for(run.queue._task, 5)
            yield run
        finally:
            if hasattr(run, "signal"):
                run.evidence["monitor"] = {
                    "status": run.signal.status, "journal_finalized": run.signal.journal_finalized,
                    "filled_legs": list(run.signal.candidate_filled_leg_indexes),
                    "confirmed_sl": dict(run.signal.sl_by_ticket), "confirmed_tp": dict(run.signal.tp_by_ticket),
                    "finalized": finalized, "anomalies": anomalies,
                    "task_done_before_cleanup": all(task.done() for task in tasks),
                    "history": {str(ticket): run.owner.broker.runtime_control.history(ticket)
                                for ticket in run.signal.all_filled_tickets},
                }
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            mt5.install(previous_runtime)


async def next_quote(run, *, settle_queue=True):
    assert run.boundary is not None and not run.boundary.done()
    assert run.owner.advance()
    run.boundary.set_result(None)
    run.boundary = await run.turns.wait_boundary(run.task)
    if settle_queue and run.queue._task is not None:
        await asyncio.wait_for(run.queue._task, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("stop", [False, True])
async def test_full_monitor_opens_protects_reads_history_and_finalizes(tmp_path, monkeypatch, direction, stop):
    async with monitored(tmp_path, monkeypatch, direction, stop=stop) as run:
        await next_quote(run)
        assert run.signal.sl_by_ticket[1001] == pytest.approx(70.6 if direction == "BUY" else 129.4)
        for count in range(1, 5):
            await next_quote(run)
            assert run.signal.candidate_filled_leg_indexes == list(range(1, count + 1))
        await next_quote(run)
        assert run.task.done() and run.task.exception() is None
        assert run.signal.status == "closed" and run.signal.journal_finalized
        assert len(run.finalized) == 1
        assert not run.anomalies
        assert not run.owner.snapshot()["positions"]
        result = run.owner.finish()
        assert not result.blockers and len(result.entries) == len(result.exits) == 5
        pnl = -604.6 if stop else 23.
        assert float(result.pnl_eur) == pytest.approx(pnl)
        assert run.finalized[0]["kwargs"]["total_pnl_usd"] == pytest.approx(pnl)
        assert run.finalized[0]["kwargs"]["account_currency"] == "EUR"
        assert_path(run, [0, 0, -80, 80, -740, -1850, -3410, -5420, round(pnl * 100), round(pnl * 100)],
                    [0, 0, .04, .04, .07, .10, .13, .16, 0, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_full_monitor_time_exit_reaches_native_close_and_finalizer(tmp_path, monkeypatch, direction):
    async with monitored(tmp_path, monkeypatch, direction, time_exit=True) as run:
        await next_quote(run)
        closes = [r for r in run.mailbox.records
                  if r["name"] == "order_send" and "position" in r["args"][0] and r["args"][0]["action"] == 1]
        assert len(closes) == 1 and closes[0]["result"]["retcode"] == 10009
        assert not run.owner.snapshot()["positions"]
        assert run.signal.basket_guard_triggered
        await next_quote(run)
        assert run.task.done() and run.task.exception() is None
        assert run.signal.status == "closed" and run.signal.journal_finalized
        assert not run.anomalies and len(run.finalized) == 1
        assert run.finalized[0]["kwargs"]["total_pnl_usd"] == pytest.approx(.8)
        result = run.owner.finish()
        assert not result.blockers and len(result.exits) == 1
        assert result.exits[0].reason == "runtime_market_close"
        assert float(result.pnl_eur) == pytest.approx(.8)
        assert_path(run, [0, 0, -80, 80, 80, 80], [0, 0, .04, 0, 0, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_unknown_positions_cannot_be_treated_as_flat_and_monitor_recovers(tmp_path, monkeypatch, direction):
    async with monitored(tmp_path, monkeypatch, direction, time_exit=True) as run:
        run.mailbox.unknown_reads.add("positions_get")
        await next_quote(run)
        assert not run.task.done() and run.signal.status == "open" and not run.finalized
        assert len(run.owner.snapshot()["positions"]) == 1
        assert not run.signal.basket_guard_triggered
        assert any(row.get("injected_unknown_read") for row in run.mailbox.records)
        run.mailbox.unknown_reads.clear()
        await next_quote(run)
        assert run.signal.basket_guard_triggered and not run.owner.snapshot()["positions"]
        await next_quote(run)
        assert run.task.done() and run.signal.status == "closed"
        assert len(run.finalized) == 1
        assert run.finalized[0]["kwargs"]["total_pnl_usd"] == pytest.approx(.8)
        result = run.owner.finish()
        assert not result.blockers and len(result.exits) == 1
        assert_path(run, [0, 0, -80, 80, 80, 80], [0, 0, .04, .04, 0, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_unknown_history_does_not_turn_missing_money_into_zero(tmp_path, monkeypatch, direction):
    async with monitored(tmp_path, monkeypatch, direction) as run:
        for _ in range(5):
            await next_quote(run)
        run.mailbox.unknown_reads.add("history_deals_get")
        await next_quote(run)
        assert not run.owner.snapshot()["positions"]
        assert run.task.done() and run.signal.status == "closed"
        assert len(run.finalized) == 1
        assert run.finalized[0]["kwargs"]["total_pnl_usd"] is None
        assert any(row.get("injected_unknown_read") for row in run.mailbox.records)
        run.evidence["runtime_money_complete"] = False
        result = run.owner.finish()
        assert float(result.pnl_eur) == pytest.approx(23.)
        # Native model money is known; runtime accounting is explicitly not.
        assert not run.signal.basket_guard_realized_by_ticket
