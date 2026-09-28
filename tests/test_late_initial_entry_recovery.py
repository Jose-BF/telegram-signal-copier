"""Late real-worker DONE recovery, not a synthetic UNKNOWN reconciler."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import listener
import position_lifecycle_monitor as monitor
from mt5_protocol import IntentState, LookupState
from mt5_runtime import MT5Runtime, mt5
from tests.test_runtime_replay_entry_controller import controller, reach_confirmation, tick


@asynccontextmanager
async def closed_initial(tmp_path, monkeypatch, direction, *, offsets=None):
    async with controller(tmp_path, monkeypatch, direction,
            [100., 98.9, 100.4, 65., 65., 65.], broker_history=True, offsets=offsets,
            trace_contract="late_initial_entry_recovery_v1") as run:
        previous = mt5.runtime
        runtime = MT5Runtime(run.client)
        opening = None
        try:
            assert (await runtime.start()).state is LookupState.FOUND
            await reach_confirmation(run)
            run.record = listener._gold_555_entry_watches[run.intent.message_id]
            run.expiry = run.record.watch.expires_at.replace(tzinfo=None)
            run.fill_time = run.clock.utcnow()
            run.mailbox.hold_responses = True
            opening = asyncio.create_task(tick(run))
            await asyncio.wait_for(run.mailbox.applied.get(), 5)
            # Let the actual client deadline return DISPATCHING while its
            # transport still owns the held native response.
            assert await asyncio.wait_for(opening, 5) == 0
            assert run.record.durable_reconcile_pending
            assert listener.state.get("canal2", run.intent.message_id) is None
            assert run.owner.advance()
            assert not run.owner.snapshot()["positions"]
            run.mailbox.release_responses()

            async def drained():
                while run.client._inflight:
                    await asyncio.sleep(.001)

            await asyncio.wait_for(drained(), 5)
            records = run.client.store.list_current_intents()
            assert len(records) == 1 and records[0].record.state is IntentState.DONE
            run.original_intent = records[0].record.intent_id
            yield run
        finally:
            run.mailbox.release_responses()
            if opening is not None:
                if not opening.done():
                    opening.cancel()
                await asyncio.gather(opening, return_exceptions=True)
            mt5.install(previous)


def native_sends(run):
    return [row for row in run.mailbox.records if row["name"] == "order_send"]


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("provider_close", [False, True])
async def test_closed_done_recovers_ticket_money_and_original_clocks_once(
        tmp_path, monkeypatch, direction, provider_close):
    async with closed_initial(tmp_path, monkeypatch, direction) as run:
        run.record.provider_close_requested = provider_close
        assert await tick(run) == 1
        signal = listener.state.get("canal2", run.intent.message_id)
        assert signal is not None and signal.all_filled_tickets == [1001]
        assert signal.market_fill_price == pytest.approx(100.4 if direction == "BUY" else 99.6)
        assert signal.candidate_first_fill_at == run.fill_time
        assert signal.candidate_entry_expires_at == run.expiry
        assert signal.requested_close_reason == ("PROVIDER_CLOSE" if provider_close else None)
        assert await asyncio.to_thread(listener._realized_pl, signal) == pytest.approx(-142.4)
        assert not run.record.durable_reconcile_pending
        assert run.intent.message_id not in listener._gold_555_entry_watches
        assert len(run.monitor_starts) == 1
        assert run.queue._task is None
        assert len(native_sends(run)) == 1

        # Redelivery must not reset cancelled legs, completed status or clocks.
        signal.lifecycle_cancelled_entry_indexes = [1, 2, 3, 4]
        signal.status, signal.journal_finalized = "closed", True
        assert await listener._open_gold_555_confirmed_intent(run.record) is signal
        assert signal.lifecycle_cancelled_entry_indexes == [1, 2, 3, 4]
        assert signal.status == "closed" and signal.journal_finalized
        assert signal.candidate_first_fill_at == run.fill_time
        assert len(run.monitor_starts) == 1 and run.queue._task is None
        assert await monitor._process_candidate_entry_tick(
            signal, SimpleNamespace(**run.owner.snapshot()["tick"]), now=run.clock.utcnow()) == 0
        assert len(native_sends(run)) == 1
        assert len(run.client.store.list_current_intents()) == 1
        assert run.client.store.get(run.original_intent).state is IntentState.DONE


@pytest.mark.parametrize("fault", [None, "unknown", "partial", "wrong_owner", "wrong_ticket",
                                  "missing_clock", "future", "nan_money", "missing_fee", "duplicate"])
def test_closed_fill_history_requires_complete_owned_finite_evidence(fault):
    opening = dict(ticket=1, position_id=1001, symbol=listener.config.MT5_SYMBOL,
        magic=listener.config.magic_for("canal2"), type=0, entry=0, time_msc=1000,
        volume=.04, price=100., profit=0., commission=-.1, swap=0., fee=-.2)
    closing = dict(opening, ticket=2, type=1, entry=1, time_msc=2000,
                   price=65., profit=-140., commission=-.1, swap=-.3, fee=-.2)
    if fault == "unknown":
        deals = None
    else:
        if fault == "partial":
            closing["volume"] = .02
        elif fault == "wrong_owner":
            opening["magic"] += 1
        elif fault == "wrong_ticket":
            opening["position_id"] = 999
        elif fault == "missing_clock":
            opening.pop("time_msc")
        elif fault == "future":
            closing["time_msc"] = 4000
        elif fault == "nan_money":
            closing["profit"] = float("nan")
        elif fault == "missing_fee":
            closing.pop("fee")
        elif fault == "duplicate":
            closing["ticket"] = 1
        deals = [SimpleNamespace(**opening), SimpleNamespace(**closing)]
    now = datetime.fromtimestamp(3, timezone.utc).replace(tzinfo=None)
    result = listener._gold_555_closed_fill_time(deals, ticket=1001, direction="BUY",
                                                fill_price=100., volume=.04, now=now)
    assert result == (datetime.fromtimestamp(1, timezone.utc).replace(tzinfo=None)
                      if fault is None else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("read", ["positions_get", "history_deals_get"])
async def test_unknown_read_keeps_recovery_pending_and_retries_without_sending(
        tmp_path, monkeypatch, direction, read):
    async with closed_initial(tmp_path, monkeypatch, direction) as run:
        run.mailbox.unknown_reads.add(read)
        assert await tick(run) == 0
        assert listener.state.get("canal2", run.intent.message_id) is None
        assert run.record.durable_reconcile_pending
        assert run.intent.message_id in listener._gold_555_entry_watches
        assert not run.monitor_starts and run.queue._task is None
        assert len(native_sends(run)) == 1
        run.mailbox.unknown_reads.clear()
        assert await tick(run) == 1
        signal = listener.state.get("canal2", run.intent.message_id)
        assert await asyncio.to_thread(listener._realized_pl, signal) == pytest.approx(-142.4)
        run.mailbox.unknown_reads.add("history_deals_get")
        assert await asyncio.to_thread(listener._realized_pl, signal) is None
        assert len(native_sends(run)) == 1
