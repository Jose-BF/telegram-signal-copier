"""The complete monitor must preserve missing execution evidence through expiry."""

import asyncio

import pytest

import listener
from mt5_protocol import IntentState
from tests.test_runtime_replay_entry_controller import assert_path
from tests.test_runtime_replay_monitor import monitored, next_quote


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("explicit_close", [False, True])
async def test_unknown_dca_cannot_be_omitted_from_final_accounting(tmp_path, monkeypatch, direction, explicit_close):
    async with monitored(tmp_path, monkeypatch, direction,
            quotes=[100., 98.9, 100.4, 98.9, 60., 60., 60.],
            offsets=[0, 1, 2, 3, 40, 1803, 1804],
            trace_contract="runtime_gold_unknown_lifecycle_v1") as run:
        run.mailbox.unknown_after_effect = True
        await next_quote(run, settle_queue=False)
        assert len(run.owner.snapshot()["positions"]) == 2
        assert run.signal.candidate_entry_reconcile_pending_indexes == [1]
        assert run.signal.dca_tickets == []
        assert run.owner.snapshot()["entries"][1]["acknowledged_ns"] is None
        run.mailbox.unknown_after_effect = False
        run.mailbox.unknown_reads.update({"history_deals_get", "history_orders_get"})
        await next_quote(run, settle_queue=False)
        assert not run.owner.snapshot()["positions"]
        assert run.signal.status == "open" and not run.finalized
        await next_quote(run, settle_queue=False)
        opens = [row for row in run.mailbox.records if row["name"] == "order_send"
                 and row["args"][0]["action"] == 1 and "position" not in row["args"][0]]
        assert len(opens) == 2
        assert run.signal.candidate_entry_reconcile_pending_indexes == [1]
        assert run.signal.status == "open" and not run.task.done() and not run.finalized
        if explicit_close:
            assert await listener._finalize_signal(run.signal, closed_by="OPERATOR_CLOSE") is False
            assert run.signal.lifecycle_last_decision["reason"] == "entry_execution_unresolved"
            assert run.signal.status == "open" and not run.signal.journal_finalized
            assert run.signal.candidate_entry_reconcile_pending_indexes == [1]
        # A known subset of deals is not a known total when an entry is unresolved.
        assert all(row["kwargs"]["total_pnl_usd"] is None for row in run.finalized)
        run.evidence["runtime_money_complete"] = False
        unconfirmed_history = run.owner.broker.runtime_control.history(1002)
        assert [row["entry"] for row in unconfirmed_history] == [0, 1]
        assert sum(row["profit"] for row in unconfirmed_history) == pytest.approx(-117.3)
        run.evidence["unconfirmed_entry_model_history"] = unconfirmed_history
        result = run.owner.finish()
        assert "market_lifecycle_incomplete_at_data_end" in result.blockers
        assert result.pnl_eur is None and len(result.entries) == len(result.exits) == 2
        assert_path(run, [0, 0, -80, -740, -27970, -27970, -27970], [0, 0, .04, .07, 0, 0, 0])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_history_recovered_dca_is_observed_without_a_native_ack(tmp_path, monkeypatch, direction):
    async with monitored(tmp_path, monkeypatch, direction,
            quotes=[100., 98.9, 100.4, 98.9, 60., 60., 60.],
            offsets=[0, 1, 2, 3, 40, 1803, 1804],
            trace_contract="runtime_gold_reconciled_lifecycle_v1") as run:
        run.mailbox.unknown_after_effect = True
        await next_quote(run, settle_queue=False)
        assert run.owner.snapshot()["entries"][1]["acknowledged_ns"] is None
        run.mailbox.unknown_after_effect = False
        await next_quote(run, settle_queue=False)
        dca = next(item.record for item in run.client.store.list_current_intents()
                   if item.intent_key.leg == "candidate-entry-1")
        assert dca.state is IntentState.DONE
        assert dca.outcome["retcode"] is None
        sends = [row["args"][0] for row in run.mailbox.records
                 if row["name"] == "order_send" and row["args"][0]["action"] == 1
                 and "position" not in row["args"][0]]
        comment = listener.gold_555_live_candidate.market_comment(run.intent.message_id, 1)
        assert sum(row["comment"] == comment for row in sends) == 1
        for _ in range(2):
            await next_quote(run, settle_queue=False)
        result = run.owner.finish()
        reconciled = [event for event in result.market_events
                      if event.ticket == "runtime_2" and event.kind == "entry_reconciled"]
        assert len(reconciled) == 1
        assert (reconciled[0].tick_index, reconciled[0].timestamp_ns) == (
            4, int(run.owner.spec.path.times_ns[4]))
        assert not [event for event in result.market_events
                    if event.ticket == "runtime_2" and event.kind == "entry_acknowledged"]
        assert result.entries[1].acknowledged_ns is None
        assert "market_lifecycle_incomplete_at_data_end" not in result.blockers


async def unknown_response(run):
    while not any(row["name"] == "trade_response" and row["args"][0]["outcome"]["state"] == "UNKNOWN"
                  for row in run.mailbox.records):
        await asyncio.sleep(.001)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_unknown_active_close_is_not_resent_as_another_effect(tmp_path, monkeypatch, direction):
    async with monitored(tmp_path, monkeypatch, direction, time_exit=True,
            trace_contract="runtime_gold_unknown_lifecycle_v1") as run:
        run.mailbox.unknown_after_effect = True
        await next_quote(run, settle_queue=False)
        await asyncio.wait_for(unknown_response(run), 5)
        assert not run.owner.snapshot()["positions"]
        run.mailbox.unknown_after_effect = False
        if not run.task.done():
            await next_quote(run, settle_queue=False)
        else:
            assert run.owner.advance()
        assert run.task.done() and len(run.finalized) == 1
        assert run.finalized[0]["kwargs"]["total_pnl_usd"] == pytest.approx(.8)
        closes = [row for row in run.mailbox.records if row["name"] == "order_send"
                  and row["args"][0]["action"] == 1 and "position" in row["args"][0]]
        assert len(closes) == 1
        result = run.owner.finish()
        assert len(result.exits) == 1 and result.pnl_eur is None
        assert "market_lifecycle_incomplete_at_data_end" in result.blockers
        assert_path(run, [0, 0, -80, 80, 80, 80], [0, 0, .04, 0, 0, 0])
