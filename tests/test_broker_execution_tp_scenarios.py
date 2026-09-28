"""Explicit hypothetical TP worlds keep trigger and fill clocks separate."""

from datetime import datetime, timedelta, timezone

import pytest

from research.dubai_iterative.broker_execution import (
    BrokerExecutionModel,
    EventTime,
    PassiveFillScenario,
    Position,
    Quote,
    SymbolConstraints,
)


BASE = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


def at(ms):
    return EventTime(BASE + timedelta(milliseconds=ms))


def model(delay_ms, *, price_mode="installed_level"):
    return BrokerExecutionModel(
        SymbolConstraints("XAUUSD", 0.01, 2, 0, 0),
        modify_processing_delay_ns=0,
        passive_exit_mode="quote_delay_scenario",
        passive_fill_scenario=PassiveFillScenario(delay_ms * 1_000_000,
                                                  price_mode),
    )


def test_deferred_tp_stays_open_and_fills_on_first_quote_after_due():
    broker = model(500)
    broker.add_position(Position("p1", "BUY", at(0), 100.0, tp=101.0))

    touch = broker.on_quote(Quote(at(1), 101.1, 101.2))
    before = broker.on_quote(Quote(at(250), 99.5, 99.6))
    filled = broker.on_quote(Quote(at(502), 99.0, 99.1))

    assert len(touch.touches) == 1 and touch.exits == ()
    assert before.exits == ()
    assert broker.position("p1").status == "closed"
    assert filled.exits[0].effective_at == at(502)
    assert filled.exits[0].first_terminal_touch_at == at(1)
    assert filled.exits[0].price == 101.0


def test_market_price_world_uses_executable_side_at_delayed_fill():
    broker = model(100, price_mode="executable_quote")
    broker.add_position(Position("p1", "SELL", at(0), 100.0, tp=99.0))
    broker.on_quote(Quote(at(1), 98.7, 98.8))
    filled = broker.on_quote(Quote(at(101), 99.2, 99.3))

    assert filled.exits[0].price == 99.3
    assert filled.exits[0].installed_level == 99.0


def test_no_quote_after_due_leaves_position_open_and_pending():
    broker = model(1000)
    broker.add_position(Position("p1", "BUY", at(0), 100.0, tp=101.0))
    broker.on_quote(Quote(at(1), 101.0, 101.1))
    broker.on_quote(Quote(at(500), 100.0, 100.1))

    assert broker.position("p1").status == "open"
    assert broker.pending_passive_count == 1


def test_modification_during_unconfirmed_scenario_fill_is_blocked():
    broker = model(1000)
    broker.add_position(Position("p1", "BUY", at(0), 100.0, tp=101.0))
    broker.on_quote(Quote(at(1), 101.0, 101.1))

    result = broker.request_modify("p1", at=at(2), sl=99.0, tp=102.0)
    assert result.status == "rejected"
    assert result.reason == "passive_fill_pending"


def test_invalid_scenario_configuration_fails_closed():
    with pytest.raises(ValueError, match="delay"):
        PassiveFillScenario(-1, "installed_level")
    with pytest.raises(ValueError, match="price_mode"):
        PassiveFillScenario(0, "unknown")
    with pytest.raises(ValueError, match="requires"):
        BrokerExecutionModel(SymbolConstraints("XAUUSD", 0.01, 2, 0, 0),
                             modify_processing_delay_ns=0,
                             passive_exit_mode="quote_delay_scenario")
