from dataclasses import replace

import pytest

from research.dubai_iterative.protection import ProtectionBlocked
from research.dubai_iterative.runtime_control import NativeCloseCommand, NativeCloseResponse
from tests.test_runtime_replay_history import history_model


def opening(owner, direction):
    result = owner.apply({"action": 1, "symbol": "XAUUSD", "magic": 111,
        "type": 0 if direction == "BUY" else 1, "volume": .04, "price": 100.,
        "sl": 90. if direction == "BUY" else 110., "comment": "C2_98766_555_L0"}, request_id="open")
    owner.observe_entry_response(owner.entry_receipts[-1].execution_id)
    return result["order"]


def command(owner, position_id, side, **changes):
    current = owner.snapshot()
    return replace(NativeCloseCommand("close", position_id, "XAUUSD", 111,
        "SELL" if side == "BUY" else "BUY", .04, 500.,
        current["quote_index"], current["time_ns"], "bot_close"), **changes)


def close(owner, request):
    receipt = owner.broker.runtime_control.apply_close(request)
    owner.risk.extend(owner.broker.runtime_control.drain_risk())
    return receipt


def ack(owner, receipt):
    current = owner.snapshot()
    return owner.broker.runtime_control.observe_close_response(NativeCloseResponse(
        receipt.execution_id, current["quote_index"], current["time_ns"]))


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_native_close_uses_current_quote_and_history_before_response(direction):
    with history_model(direction) as owner:
        position = opening(owner, direction)
        assert owner.advance()
        receipt = close(owner, command(owner, position, direction))
        assert receipt.retcode == 10009
        assert receipt.price == pytest.approx(104.8 if direction == "BUY" else 95.2)
        assert not owner.snapshot()["positions"]
        deals = owner.broker.runtime_control.history(position)
        assert deals[-1]["ticket"] == receipt.native_result()["deal"]
        assert deals[-1]["order"] == receipt.native_result()["order"]
        assert deals[-1]["comment"] == "bot_close"
        assert deals[-1]["profit"] == pytest.approx(19.2)
        assert owner.risk[-1]["realized_minor"] == 1920 and owner.risk[-1]["floating_minor"] == 0
        assert owner.advance()
        assert ack(owner, receipt) is True and ack(owner, receipt) is False
        result = owner.finish()
        assert not result.blockers and len(result.exits) == 1 and float(result.pnl_eur) == pytest.approx(19.2)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_partial_native_closes_keep_remaining_protection_and_unique_deals(direction):
    with history_model(direction) as owner:
        position = opening(owner, direction)
        assert owner.advance()
        first = close(owner, command(owner, position, direction, volume=.02))
        remaining = owner.snapshot()["positions"][0]
        assert remaining["volume"] == .02 and remaining["sl"] == (90. if direction == "BUY" else 110.)
        assert ack(owner, first)
        second = close(owner, command(owner, position, direction, volume=.02, request_id="second"))
        assert ack(owner, second)
        assert first.ticket != second.ticket and first.deal_id != second.deal_id
        assert not owner.snapshot()["positions"]
        result = owner.finish()
        assert not result.blockers and len(result.exits) == 2 and float(result.pnl_eur) == pytest.approx(19.2)
        deals = owner.broker.runtime_control.history(position)
        assert len(deals) == len({d["ticket"] for d in deals}) == 3
        assert sum(d["volume"] for d in deals if d["entry"] == 1) == .04


@pytest.mark.parametrize("changes,retcode", [
    ({"position": 9000}, 10036), ({"magic": 222}, 10013), ({"symbol": "OTHER"}, 10013),
    ({"direction": "BUY"}, 10013), ({"volume": .05}, 10038), ({"volume": .005}, 10038),
])
def test_rejected_close_has_no_effect_but_explicit_response(changes, retcode):
    with history_model() as owner:
        position = opening(owner, "BUY")
        before = owner.snapshot()
        receipt = close(owner, command(owner, position, "BUY", **changes))
        assert receipt.retcode == retcode
        assert owner.snapshot() == before
        assert ack(owner, receipt)


def test_closed_position_cannot_close_twice_or_generate_more_profit():
    with history_model() as owner:
        position = opening(owner, "BUY")
        first = close(owner, command(owner, position, "BUY"))
        second = close(owner, command(owner, position, "BUY"))
        assert first.retcode == 10009 and second.retcode == 10036
        assert ack(owner, first) and ack(owner, second)
        result = owner.finish()
        assert not result.blockers and len(result.exits) == 1
        assert float(result.pnl_eur) == pytest.approx(-.8)
        assert [r.kind for r in result.market_events].count("close_requested") == 2


def test_close_without_response_is_not_a_verified_complete_result():
    with history_model() as owner:
        position = opening(owner, "BUY")
        receipt = close(owner, command(owner, position, "BUY"))
        result = owner.finish()
        assert len(result.exits) == 1 and result.pnl_eur is None
        assert "market_lifecycle_incomplete_at_data_end" in result.blockers
        with pytest.raises(ProtectionBlocked, match="not_active"):
            ack(owner, receipt)


@pytest.mark.parametrize("free_slots", [0, 1])
def test_close_risk_budget_fails_without_partial_effect(free_slots):
    with history_model() as owner:
        position = opening(owner, "BUY")
        before = owner.snapshot()
        owner.risk_count = 10_000 - free_slots
        with pytest.raises(ProtectionBlocked, match="risk_budget"):
            close(owner, command(owner, position, "BUY"))
        assert owner.snapshot() == before
        assert len(owner.broker.runtime_control.history(position)) == 1
        result = owner.finish()
        assert not result.exits and result.pnl_eur is None
