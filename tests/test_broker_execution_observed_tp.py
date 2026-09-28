"""Observed-fill mode keeps terminal touches separate from broker deals."""

from datetime import datetime, timedelta, timezone

import pytest

from research.dubai_iterative.broker_execution import (
    BrokerExecutionModel,
    EventTime,
    Position,
    Quote,
    SymbolConstraints,
)


BASE = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


def at(ms):
    return EventTime(BASE + timedelta(milliseconds=ms))


def model():
    return BrokerExecutionModel(
        SymbolConstraints("XAUUSD", 0.01, 2, 0, 0),
        modify_processing_delay_ns=0,
        passive_exit_mode="observed_fill",
    )


def test_touch_keeps_position_open_until_independent_broker_deal():
    broker = model()
    broker.add_position(Position("p1", "BUY", at(0), 100.0, tp=101.0))

    first = broker.on_quote(Quote(at(1), 101.1, 101.2))
    later = broker.on_quote(Quote(at(2), 101.2, 101.3))

    assert first.exits == ()
    assert len(first.touches) == 1
    assert first.touches[0].reason == "take_profit"
    assert first.touches[0].observed_at == at(1)
    assert later.touches == ()
    assert broker.position("p1").status == "open"

    exit_row = broker.confirm_observed_exit("p1", at=at(4), price=101.0,
                                            reason="take_profit")
    assert exit_row.effective_at == at(4)
    assert exit_row.first_terminal_touch_at == at(1)
    assert broker.position("p1").status == "closed"


def test_observed_fill_without_retained_touch_is_not_invented_or_rejected():
    broker = model()
    broker.add_position(Position("p1", "SELL", at(0), 100.0, tp=99.0))
    broker.on_quote(Quote(at(1), 99.9, 100.0))

    exit_row = broker.confirm_observed_exit("p1", at=at(2), price=99.0,
                                            reason="take_profit")
    assert exit_row.first_terminal_touch_at is None
    assert exit_row.price == 99.0


def test_fill_cannot_be_backdated_or_assigned_same_clock_as_terminal_quote():
    broker = model()
    broker.add_position(Position("p1", "BUY", at(0), 100.0, tp=101.0))
    broker.on_quote(Quote(at(1), 101.0, 101.1))

    with pytest.raises(ValueError, match="after model clock"):
        broker.confirm_observed_exit("p1", at=at(1), price=101.0,
                                     reason="take_profit")
    assert broker.position("p1").status == "open"


def test_quote_after_observed_fill_needs_unambiguous_later_clock():
    broker = model()
    broker.add_position(Position("p1", "BUY", at(0), 100.0, tp=101.0))
    broker.confirm_observed_exit("p1", at=at(2), price=101.0,
                                 reason="take_profit")

    with pytest.raises(ValueError, match="after model clock"):
        broker.on_quote(Quote(at(2), 101.0, 101.1))


def test_level_installs_only_at_observed_receipt_and_touches_later():
    broker = model()
    broker.add_position(Position("p1", "BUY", at(0), 100.0))
    request = broker.request_modify("p1", at=at(1), sl=None, tp=101.0)
    crossed_before_receipt = broker.on_quote(Quote(at(2), 101.1, 101.2))
    installed = broker.confirm_observed_modify(
        request.request_id, at=at(3), status="accepted")
    triggered = broker.on_quote(Quote(at(4), 101.0, 101.1))

    assert crossed_before_receipt.modifications == ()
    assert crossed_before_receipt.touches == ()
    assert installed.status == "accepted"
    assert installed.effective_at == at(3)
    assert len(triggered.touches) == 1
    assert triggered.touches[0].observed_at == at(4)


def test_observed_modify_rejection_keeps_old_level_and_request_cannot_be_reused():
    broker = model()
    broker.add_position(Position("p1", "BUY", at(0), 100.0, tp=102.0))
    request = broker.request_modify("p1", at=at(1), sl=None, tp=101.0)
    broker.on_quote(Quote(at(2), 100.5, 100.6))

    result = broker.confirm_observed_modify(
        request.request_id, at=at(3), status="rejected", reason="invalid_stops")

    assert result.status == "rejected"
    assert broker.position("p1").tp == 102.0
    assert broker.position("p1").revision == 0
    with pytest.raises(ValueError, match="pending"):
        broker.confirm_observed_modify(
            request.request_id, at=at(4), status="accepted")


def test_observed_modify_response_cannot_share_clock_with_terminal_quote():
    broker = model()
    broker.add_position(Position("p1", "BUY", at(0), 100.0))
    request = broker.request_modify("p1", at=at(1), sl=None, tp=101.0)
    broker.on_quote(Quote(at(2), 100.5, 100.6))

    with pytest.raises(ValueError, match="after model clock"):
        broker.confirm_observed_modify(
            request.request_id, at=at(2), status="accepted")
    assert broker.position("p1").tp is None


def test_unknown_observed_fill_mode_is_rejected():
    with pytest.raises(ValueError, match="passive_exit_mode"):
        BrokerExecutionModel(SymbolConstraints("XAUUSD", 0.01, 2, 0, 0),
                             modify_processing_delay_ns=0,
                             passive_exit_mode="magic_delay")
