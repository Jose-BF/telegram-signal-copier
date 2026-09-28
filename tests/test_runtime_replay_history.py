from contextlib import contextmanager
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime

import pytest

from entry_history_reconciliation import match_market_entry
from mt5_protocol import BrokerRequest, IntentKey
from mt5_trade_protocol import TradePreparation
from research.dubai_iterative.runtime_control import NativeEntryReconciliation, RuntimeBrokerIdentity
from tests.runtime_replay_support import ReplayOwner, RuntimeMailbox
from tests.test_client_terminal_close import execution, strategy, tape
from tests.test_shared_policy_replay import spec


@contextmanager
def history_model(direction="BUY"):
    quotes = [100., 105., 80., 80.] if direction == "BUY" else [100., 95., 120., 120.]
    item = spec("canal2_history", [], tape=tape(quotes, direction=direction),
                strategy=strategy().with_change(provider_management_mode="ignore"), execution=execution())
    identity = RuntimeBrokerIdentity("XAUUSD", 111, 1000, entry_owner="external_runtime",
                                    history_cost_profile="synthetic_zero_commission_fee_v1")
    owner = ReplayOwner(item, identity)
    try:
        assert owner.advance()
        yield owner
    finally:
        owner.close()


@pytest.mark.parametrize("change", [
    {"request_id": "another-request"}, {"order": 9999}, {"deal": 9999},
    {"magic": 222}, {"comment": "another-comment"}, {"price": 101.},
])
def test_history_observation_must_match_native_entry_before_resolving_wait(change):
    with history_model() as owner:
        receipt = owner.apply({"action": 1, "symbol": "XAUUSD", "magic": 111,
            "type": 0, "volume": .04, "price": 100., "comment": "C2_98766_555_L0"},
            request_id="open")
        clock = owner.snapshot()
        observation = NativeEntryReconciliation("open", receipt["order"], receipt["deal"],
            "XAUUSD", 111, "BUY", "C2_98766_555_L0", receipt["volume"], receipt["price"],
            clock["quote_index"], clock["time_ns"])
        with pytest.raises(ValueError):
            owner.broker.runtime_control.observe_entry_reconciliation(replace(observation, **change))
        result = owner.finish()
        assert not [event for event in result.market_events if event.kind == "entry_reconciled"]
        assert "market_lifecycle_incomplete_at_data_end" in result.blockers


def test_history_observation_resolves_wait_without_native_ack():
    with history_model() as owner:
        receipt = owner.apply({"action": 1, "symbol": "XAUUSD", "magic": 111,
            "type": 0, "volume": .04, "price": 100., "comment": "C2_98766_555_L0"},
            request_id="open")
        clock = owner.snapshot()
        observation = NativeEntryReconciliation("open", receipt["order"], receipt["deal"],
            "XAUUSD", 111, "BUY", "C2_98766_555_L0", receipt["volume"], receipt["price"],
            clock["quote_index"], clock["time_ns"])
        assert owner.broker.runtime_control.observe_entry_reconciliation(observation) is True
        assert owner.broker.runtime_control.observe_entry_reconciliation(observation) is False
        result = owner.finish()
        assert len([event for event in result.market_events if event.kind == "entry_reconciled"]) == 1
        assert not [event for event in result.market_events if event.kind == "entry_acknowledged"]
        assert result.entries[0].acknowledged_ns is None


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_native_comment_profit_and_deals_come_from_the_same_book(direction):
    with history_model(direction) as owner:
        result = owner.apply({"action": 1, "symbol": "XAUUSD", "magic": 111,
            "type": 0 if direction == "BUY" else 1, "volume": .04, "price": 100.,
            "sl": 90. if direction == "BUY" else 110., "comment": "C2_98766_555_L0"}, request_id="open")
        position = owner.snapshot()["positions"][0]
        assert position["comment"] == "C2_98766_555_L0"
        assert position["profit"] == pytest.approx(-.8)
        history = owner.broker.runtime_control.history
        opening = history(result["order"])
        assert len(opening) == 1 and opening[0]["entry"] == 0
        assert opening[0]["ticket"] == result["deal"]
        assert opening[0]["position_id"] == result["order"]
        assert opening[0]["price"] == 100. and opening[0]["comment"] == position["comment"]
        assert history(12345) == []
        owner.observe_entry_response(owner.entry_receipts[0].execution_id)
        assert owner.advance()
        assert owner.snapshot()["positions"][0]["profit"] == pytest.approx(19.2)
        assert history(result["order"]) == opening
        assert owner.advance()
        assert not owner.snapshot()["positions"]
        deals = history(result["order"])
        assert [row["entry"] for row in deals] == [0, 1]
        assert len({row["ticket"] for row in deals}) == 2
        assert deals[1]["reason"] == 4 and deals[1]["profit"] == pytest.approx(-80.8)
        assert deals[1]["time_msc"] == owner.snapshot()["time_ns"] // 1_000_000
        assert all(row["commission"] == row["fee"] == row["swap"] == 0. for row in deals)
        deals[0]["profit"] = 999
        assert history(result["order"])[0]["profit"] == 0.
        report = owner.finish()
        assert not report.blockers and float(report.pnl_eur) == pytest.approx(-80.8)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_temporal_history_before_ack_matches_the_same_entry(direction):
    with history_model(direction) as owner:
        access = owner.broker.runtime_control
        snapshot = owner.snapshot()
        second = snapshot["time_ns"] // 1_000_000_000
        tick = snapshot["tick"]
        assert access.history_range("deals", second - 1, second - 1, "XAUUSD") == []
        assert access.history_range("orders", second - 1, second - 1, "XAUUSD") == []
        side = 0 if direction == "BUY" else 1
        comment = "C2_98766_555_L0"
        native = {"action": 1, "symbol": "XAUUSD", "magic": 111,
                  "type": side, "volume": .04, "price": 100., "comment": comment,
                  "sl": 90. if direction == "BUY" else 110.}
        receipt = owner.apply(native, request_id="open")
        before_read = owner.snapshot(), list(owner.risk)
        deals = access.history_range("deals", second, second, "XAUUSD")
        orders = access.history_range("orders", second, second, "XAUUSD")
        assert len(deals) == len(orders) == 1
        assert deals[0]["ticket"] == receipt["deal"]
        assert orders[0]["ticket"] == receipt["order"]
        assert deals[0]["time_msc"] == orders[0]["time_done_msc"] == tick["time_msc"]
        assert owner.snapshot()["positions"][0]["ticket"] == receipt["order"]
        request = BrokerRequest.create(
            IntentKey("demo/7", "canal2", "canal2_98766", 0, "entry-0", "OPEN_MARKET", 0),
            {"symbol": "XAUUSD", "direction": direction, "volume": .04,
             "sl": native["sl"], "tp": None, "loss_budget": None,
             "protection_policy": "required", "magic": 111,
             "comment": comment, "deviation": 30},
            request_id="request", attempt_id="attempt", action_id="action",
        )
        preparation = TradePreparation(
            request.request_id, request.intent_id, request.attempt_id,
            request.action_id, "worker", 123, 1., native,
            {"source_tick": tick},
        )
        match = match_market_entry(
            request, preparation, deals=deals, orders=orders,
            date_from_msc=tick["time_msc"], date_to_msc=tick["time_msc"],
            account_fingerprint="demo/7",
        )
        assert (match.order, match.deal_ids, match.volume, match.price) == (
            receipt["order"], (receipt["deal"],), .04, receipt["price"])
        deals[0]["price"] = 999.
        assert access.history_range("deals", second, second, "XAUUSD")[0]["price"] == receipt["price"]
        assert (owner.snapshot(), owner.risk) == before_read


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_temporal_history_adds_native_close_only_after_its_quote(direction):
    with history_model(direction) as owner:
        access = owner.broker.runtime_control
        start = owner.snapshot()["time_ns"] // 1_000_000_000
        side = 0 if direction == "BUY" else 1
        receipt = owner.apply({"action": 1, "symbol": "XAUUSD", "magic": 111,
            "type": side, "volume": .04, "price": 100.,
            "sl": 90. if direction == "BUY" else 110.,
            "comment": "C2_98766_555_L0"}, request_id="open")
        owner.observe_entry_response(owner.entry_receipts[0].execution_id)
        assert owner.advance()
        assert len(access.history_range("deals", start, start + 1, "XAUUSD")) == 1
        assert owner.advance()
        end = owner.snapshot()["time_ns"] // 1_000_000_000
        deals = access.history_range("deals", start, end, "XAUUSD")
        orders = access.history_range("orders", start, end, "XAUUSD")
        assert [row["entry"] for row in deals] == [0, 1]
        assert len(orders) == 2
        assert orders[0]["ticket"] == receipt["order"]
        assert orders[1]["ticket"] == deals[1]["order"]
        assert orders[1]["position_id"] == receipt["order"]
        assert orders[1]["type"] == 1 - side
        assert len(access.history_range("deals", start, end - 1, "XAUUSD")) == 1
        assert len(access.history_range("orders", start, end - 1, "XAUUSD")) == 1


def test_temporal_history_rejects_invalid_or_ambiguous_ranges():
    with history_model() as owner:
        history_range = owner.broker.runtime_control.history_range
        second = owner.snapshot()["time_ns"] // 1_000_000_000
        invalid = [
            ("deals", -1, second, "XAUUSD"),
            ("deals", second + 1, second, "XAUUSD"),
            ("deals", 0, second, "XAUUSD"),
            ("deals", second, 2**53, "XAUUSD"),
            ("deals", second, second + 2, "XAUUSD"),
            ("deals", second, second, "XAU*"),
            ("orders", datetime(2026, 1, 1), second, "XAUUSD"),
            ("positions", second, second, "XAUUSD"),
        ]
        for values in invalid:
            with pytest.raises(ValueError):
                history_range(*values)


@pytest.mark.asyncio
async def test_temporal_history_uses_the_replay_mailbox_without_future_rows():
    with history_model() as owner:
        snapshot = owner.snapshot()
        second = snapshot["time_ns"] // 1_000_000_000
        owner.apply({"action": 1, "symbol": "XAUUSD", "magic": 111,
                     "type": 0, "volume": .04, "price": 100., "sl": 90.,
                     "comment": "C2_98766_555_L0"}, request_id="open")
        box = RuntimeMailbox(owner)
        try:
            deals = await asyncio.to_thread(
                box.call, "history_deals_get", second, second, group="XAUUSD")
            orders = await asyncio.to_thread(
                box.call, "history_orders_get", second, second, group="XAUUSD")
            assert len(deals) == len(orders) == 1
            assert deals[0]["order"] == orders[0]["ticket"]
            assert await asyncio.to_thread(
                box.call, "history_orders_get", second - 1, second - 1, group="XAUUSD") == []
            box.unknown_reads.add("history_orders_get")
            assert await asyncio.to_thread(
                box.call, "history_orders_get", second, second, group="XAUUSD") is None
            with pytest.raises(ValueError, match="history"):
                await asyncio.to_thread(box.call, "history_deals_get")
        finally:
            box.abort()
            await box.task
