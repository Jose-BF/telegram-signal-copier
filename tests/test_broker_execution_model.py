"""Public-contract regressions for the offline broker lifecycle hypothesis."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

import pytest

from research.dubai_iterative.broker_execution import (
    BrokerExecutionModel,
    EventTime,
    Position,
    Quote,
    SymbolConstraints,
)


BASE = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _time(milliseconds: int = 0, *, nanosecond: int = 0) -> EventTime:
    return EventTime(BASE + timedelta(milliseconds=milliseconds), nanosecond)


def _constraints(
    *,
    min_stop_distance_points: int | None = 20,
    freeze_distance_points: int | None = 10,
    restriction_hypothesis: str | None = "fixed_point_distance",
) -> SymbolConstraints:
    return SymbolConstraints(
        symbol="TEST",
        point=0.01,
        digits=2,
        min_stop_distance_points=min_stop_distance_points,
        freeze_distance_points=freeze_distance_points,
        restriction_hypothesis=restriction_hypothesis,
    )


def _model(*, delay_ms: int = 100, constraints=None) -> BrokerExecutionModel:
    return BrokerExecutionModel(
        constraints or _constraints(),
        modify_processing_delay_ns=delay_ms * 1_000_000,
    )


@pytest.mark.parametrize(
    ("side", "sl", "tp", "bid", "ask", "expected_reason", "expected_price"),
    [
        ("BUY", 99.00, 101.00, 101.10, 101.30, "take_profit", 101.10),
        ("BUY", 99.00, 101.00, 98.90, 99.10, "stop_loss", 98.90),
        ("SELL", 101.00, 99.00, 98.80, 99.00, "take_profit", 99.00),
        ("SELL", 101.00, 99.00, 100.90, 101.10, "stop_loss", 101.10),
    ],
)
def test_passive_exits_use_bid_for_buy_and_ask_for_sell(
    side, sl, tp, bid, ask, expected_reason, expected_price,
):
    model = _model()
    model.add_position(Position("p1", side, _time(), 100.00, sl=sl, tp=tp))

    update = model.on_quote(Quote(_time(1), bid=bid, ask=ask))

    assert len(update.exits) == 1
    assert update.exits[0].reason == expected_reason
    assert update.exits[0].price == expected_price
    assert update.exits[0].effective_at == _time(1)
    assert model.position("p1").status == "closed"


@pytest.mark.parametrize(
    ("side", "bid", "ask", "wrong_tp"),
    [
        ("BUY", 100.00, 100.20, 99.80),
        ("SELL", 99.80, 100.00, 100.20),
    ],
)
def test_wrong_side_tp_is_rejected_for_both_directions(side, bid, ask, wrong_tp):
    model = _model()
    model.add_position(Position("p1", side, _time(), 100.00))
    model.request_modify("p1", at=_time(), sl=None, tp=wrong_tp)

    update = model.on_quote(Quote(_time(100), bid=bid, ask=ask))

    assert update.modifications[0].status == "rejected"
    assert update.modifications[0].reason == "tp_wrong_side"
    assert update.modifications[0].effective_at is None
    assert model.position("p1").status == "open"
    assert model.position("p1").tp is None
    assert update.exits == ()


def test_delayed_sell_tp_crossed_before_processing_does_not_invent_a_fill():
    model = _model()
    model.add_position(Position("p1", "SELL", _time(), 100.00, sl=105.00))
    source = model.on_quote(Quote(_time(1), bid=99.80, ask=100.00))
    assert source.exits == ()
    model.request_modify("p1", at=_time(1), sl=104.00, tp=99.00)

    update = model.on_quote(Quote(_time(101), bid=98.70, ask=98.90))

    assert update.modifications[0].reason == "tp_wrong_side"
    assert update.exits == ()
    state = model.position("p1")
    assert state.status == "open"
    assert state.sl == 105.00
    assert state.tp is None
    assert state.revision == 0


def test_after_price_returns_retry_can_install_then_a_later_quote_can_trigger():
    model = _model()
    model.add_position(Position("p1", "SELL", _time(), 100.00, sl=105.00))
    model.request_modify("p1", at=_time(), sl=104.00, tp=99.00)
    rejected = model.on_quote(Quote(_time(100), bid=98.70, ask=98.90))
    assert rejected.modifications[0].status == "rejected"

    model.request_modify("p1", at=_time(100), sl=104.00, tp=99.00)
    accepted = model.on_quote(Quote(_time(250), bid=99.80, ask=100.00))

    assert accepted.modifications[0].status == "accepted"
    assert accepted.modifications[0].requested_at == _time(100)
    assert accepted.modifications[0].processed_at == _time(250)
    assert accepted.modifications[0].effective_at == _time(250)
    assert accepted.exits == ()
    assert model.position("p1").levels_effective_at == _time(250)

    triggered = model.on_quote(Quote(_time(251), bid=98.70, ask=98.90))
    assert triggered.exits[0].reason == "take_profit"
    assert triggered.exits[0].price == 98.90


def test_old_stop_remains_active_while_modify_is_not_due():
    model = _model()
    model.add_position(Position("p1", "BUY", _time(), 100.00, sl=99.00))
    submission = model.request_modify("p1", at=_time(), sl=98.00, tp=102.00)

    update = model.on_quote(Quote(_time(50), bid=98.90, ask=99.10))

    assert update.exits[0].reason == "stop_loss"
    assert update.exits[0].installed_level == 99.00
    assert update.modifications == ()
    assert model.inflight_count == 1
    processed = model.on_quote(Quote(_time(100), bid=98.80, ask=99.00))
    assert processed.modifications[0].request_id == submission.request_id
    assert processed.modifications[0].reason == "position_closed"
    assert processed.modifications[0].processed_at.epoch_ns >= submission.processing_not_before_ns
    assert model.inflight_count == 0
    assert model.position("p1").sl == 99.00


def test_atomic_modify_failure_keeps_both_previously_installed_levels():
    model = _model()
    model.add_position(
        Position("p1", "BUY", _time(), 100.00, sl=99.00, tp=101.00)
    )
    model.request_modify("p1", at=_time(), sl=98.00, tp=99.50)

    update = model.on_quote(Quote(_time(100), bid=100.00, ask=100.20))

    assert update.modifications[0].reason == "tp_wrong_side"
    state = model.position("p1")
    assert (state.sl, state.tp, state.revision) == (99.00, 101.00, 0)


def test_installed_exit_has_priority_over_modify_due_at_the_same_timestamp():
    model = _model()
    model.add_position(Position("p1", "BUY", _time(), 100.00, sl=99.00))
    model.request_modify("p1", at=_time(), sl=98.00, tp=102.00)

    update = model.on_quote(Quote(_time(100), bid=98.90, ask=99.10))

    assert update.exits[0].reason == "stop_loss"
    assert update.exits[0].installed_level == 99.00
    assert update.modifications[0].reason == "position_closed"
    assert model.position("p1").levels_effective_at == _time()


def test_missing_quote_cannot_process_a_due_request_or_change_state():
    model = _model()
    model.add_position(Position("p1", "BUY", _time(), 100.00, sl=99.00))
    model.request_modify("p1", at=_time(), sl=98.00, tp=102.00)

    with pytest.raises(ValueError, match="quote is required"):
        model.on_quote(None)

    assert model.inflight_count == 1
    assert model.position("p1").sl == 99.00


def test_one_inflight_request_per_position_rejects_overlap_without_future_mutation():
    model = _model()
    model.add_position(Position("p1", "BUY", _time(), 100.00, sl=99.00))
    first = model.request_modify("p1", at=_time(), sl=98.50, tp=102.00)
    second = model.request_modify("p1", at=_time(), sl=97.00, tp=103.00)

    assert first.status == "queued"
    assert second.status == "rejected"
    assert second.reason == "modify_in_flight"

    update = model.on_quote(Quote(_time(100), bid=100.00, ask=100.20))
    assert [row.request_id for row in update.modifications] == [first.request_id]
    state = model.position("p1")
    assert (state.sl, state.tp, state.revision) == (98.50, 102.00, 1)
    assert model.inflight_count == 0


def test_unknown_symbol_restrictions_reject_instead_of_guessing():
    model = _model(
        constraints=_constraints(
            min_stop_distance_points=None,
            freeze_distance_points=None,
            restriction_hypothesis=None,
        )
    )
    model.add_position(Position("p1", "BUY", _time(), 100.00))
    model.request_modify("p1", at=_time(), sl=99.00, tp=101.00)

    update = model.on_quote(Quote(_time(100), bid=100.00, ask=100.20))

    assert update.modifications[0].reason == "symbol_restrictions_unknown"
    assert model.position("p1").revision == 0


@pytest.mark.parametrize(
    ("side", "bid", "ask", "sl", "tp"),
    [
        ("BUY", 100.00, 100.02, 99.80, 100.20),
        ("SELL", 99.98, 100.00, 100.20, 99.80),
    ],
)
def test_exact_official_stop_distance_boundary_is_accepted(
    side, bid, ask, sl, tp,
):
    model = _model()
    model.add_position(Position("p1", side, _time(), 100.00))
    model.request_modify("p1", at=_time(), sl=sl, tp=tp)

    update = model.on_quote(Quote(_time(100), bid=bid, ask=ask))

    assert update.modifications[0].status == "accepted"
    assert model.position("p1").sl == sl
    assert model.position("p1").tp == tp


def test_below_official_stop_distance_is_rejected():
    model = _model()
    model.add_position(Position("p1", "BUY", _time(), 100.00))
    model.request_modify("p1", at=_time(), sl=None, tp=100.19)

    update = model.on_quote(Quote(_time(100), bid=100.00, ask=100.02))

    assert update.modifications[0].reason == "tp_within_min_stop_distance"
    assert model.position("p1").tp is None


def test_fixed_freeze_distance_hypothesis_is_applied_separately():
    model = _model(
        constraints=_constraints(
            min_stop_distance_points=10,
            freeze_distance_points=20,
        )
    )
    model.add_position(Position("p1", "BUY", _time(), 100.00))
    model.request_modify("p1", at=_time(), sl=None, tp=100.15)

    update = model.on_quote(Quote(_time(100), bid=100.00, ask=100.02))

    assert update.modifications[0].reason == "tp_within_freeze_distance"
    assert model.position("p1").tp is None


def test_event_time_is_timezone_aware_and_preserves_nanoseconds():
    with pytest.raises(ValueError, match="timezone-aware"):
        EventTime(datetime(2026, 1, 1, 10, 0))
    assert _time(nanosecond=999).epoch_ns == _time().epoch_ns + 999


def test_quotes_and_requests_must_be_monotonic():
    model = _model()
    model.add_position(Position("p1", "BUY", _time(), 100.00))
    model.on_quote(Quote(_time(10), bid=100.00, ask=100.20))

    with pytest.raises(ValueError, match="strictly increasing"):
        model.on_quote(Quote(_time(10), bid=100.00, ask=100.20))
    with pytest.raises(ValueError, match="before model clock"):
        model.request_modify("p1", at=_time(9), sl=99.00, tp=101.00)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: BrokerExecutionModel(_constraints(), modify_processing_delay_ns=-1),
        lambda: SymbolConstraints("TEST", -0.01, 2, 20, 10),
        lambda: SymbolConstraints("TEST", 0.01, 2, -1, 10),
        lambda: SymbolConstraints("TEST", 0.01, 2, 20, -1),
        lambda: EventTime(BASE, -1),
        lambda: EventTime(BASE, 1_000),
        lambda: Quote(_time(), bid=math.nan, ask=100.20),
        lambda: Quote(_time(), bid=100.20, ask=100.00),
        lambda: Position("p1", "BUY", _time(), -100.00),
    ],
)
def test_invalid_or_negative_contract_parameters_fail_closed(factory):
    with pytest.raises(ValueError):
        factory()


def test_unsupported_restriction_hypothesis_fails_closed():
    with pytest.raises(ValueError, match="unsupported restriction hypothesis"):
        _constraints(restriction_hypothesis="pretend_mt5_exact")


def test_unaligned_request_price_is_rejected_without_rounding():
    model = _model()
    model.add_position(Position("p1", "BUY", _time(), 100.00))

    with pytest.raises(ValueError, match="point-aligned"):
        model.request_modify("p1", at=_time(), sl=99.001, tp=101.00)
