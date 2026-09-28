"""External native close sends have separate, explicitly observed responses."""

import pytest

from research.dubai_iterative.protection import ProtectionBlocked
from tests.test_market_external_response import book, snapshot


def request(market, *, ticket="leg-0", reason="runtime_close"):
    return market.request_external_close(ticket, 0, 100, 100., .04, reason)


@pytest.mark.parametrize("delay", [0, 250])
@pytest.mark.parametrize("rejection", [None, "position_already_closed"])
def test_external_close_requires_response_not_quote_clock(delay, rejection):
    market = book(delay=delay)
    request_id = request(market)
    state = market.requests[0]
    assert request_id == state.request_id == 1
    assert not state.entry and state.response_owner == "external_runtime"
    assert not market.closes and not market.entries and market.pending
    assert not market.entry_waiting
    assert market.due_closes(99, 10**12) == ()
    market.finish_external_close(request_id, 1, 200, 99., rejection=rejection)
    assert state.processed_ns == 200 and state.acknowledgement_ns is None
    before = snapshot(market)
    for now in (200, 250_000_200, 10**12):
        assert market.acknowledge(99, now) == {}
        assert market.due_closes(99, now) == ()
        assert snapshot(market) == before
    assert market.pending
    assert market.acknowledge_external_close(request_id, 2, 300) is True
    assert not market.pending
    assert state.acknowledged and state.acknowledgement_ns == 300
    assert [event.kind for event in market.events] == [
        "close_requested", "close_rejected" if rejection else "close_filled", "close_acknowledged"]
    assert [event.request_id for event in market.events] == [request_id] * 3
    assert [event.timestamp_ns for event in market.events] == [100, 200, 300]
    assert [event.tick_index for event in market.events] == [0, 1, 2]
    assert market.events[-1].price == (None if rejection else 99.)
    assert market.events[-1].reason == (rejection or "accepted")


@pytest.mark.parametrize("processed", [False, True])
def test_external_close_ack_before_effect_or_processing_clock_is_invalid(processed):
    market = book()
    request_id = request(market)
    if processed:
        market.finish_external_close(request_id, 1, 200, 99.)
    before = snapshot(market)
    with pytest.raises(ValueError):
        market.acknowledge_external_close(request_id, 1, 199)
    assert snapshot(market) == before


def test_same_clock_response_and_duplicate_ack_at_capacity():
    market = book(max_events=3)
    request_id = request(market)
    market.finish_external_close(request_id, 1, 200, 99.)
    assert market.acknowledge_external_close(request_id, 1, 200) is True
    before = snapshot(market)
    assert market.acknowledge_external_close(request_id, 2, 300) is False
    assert snapshot(market) == before


@pytest.mark.parametrize("rejection", [None, "position_already_closed"])
def test_new_native_send_same_ticket_is_separate_while_first_response_unknown(rejection):
    market = book()
    first = request(market)
    market.finish_external_close(first, 1, 200, 99., rejection=rejection)
    second = request(market)
    assert (first, second) == (1, 2)
    market.finish_external_close(second, 2, 300, None, rejection="position_already_closed")
    assert market.acknowledge_external_close(second, 3, 400) is True
    assert market.pending and not market.requests[0].acknowledged
    assert market.requests[0].outcome_reason == (rejection or "accepted")
    assert market.acknowledge_external_close(first, 4, 500) is True
    assert not market.pending and not market.closes
    assert [event.request_id for event in market.events] == [1, 1, 2, 2, 2, 1]


@pytest.mark.parametrize("operation", ["finish", "ack"])
@pytest.mark.parametrize("owner,entry", [("quote_clock", False), ("quote_clock", True),
                                         ("external_runtime", True)])
def test_other_request_kinds_cannot_use_external_close_response(operation, owner, entry):
    market = book()
    market.request("leg-0", 0, 100, 100., .04, "control", entry=entry, response_owner=owner)
    market.finish("leg-0", 1, 200, 99., entry=entry)
    before = snapshot(market)
    with pytest.raises(ValueError):
        if operation == "finish":
            market.finish_external_close(1, 2, 300, 98.)
        else:
            market.acknowledge_external_close(1, 2, 300)
    assert snapshot(market) == before


@pytest.mark.parametrize("operation", ["finish", "ack"])
@pytest.mark.parametrize("request_id", [0, -1, 2, True, 1., "1", None])
def test_invalid_request_identity_does_not_mutate(operation, request_id):
    market = book()
    request(market)
    market.finish_external_close(1, 1, 200, 99.)
    before = snapshot(market)
    with pytest.raises(ValueError):
        if operation == "finish":
            market.finish_external_close(request_id, 2, 300, 98.)
        else:
            market.acknowledge_external_close(request_id, 2, 300)
    assert snapshot(market) == before


@pytest.mark.parametrize("stage", ["request", "finish", "ack"])
def test_capacity_precedes_external_close_mutation(stage):
    market = book(max_events=2 if stage == "ack" else 1)
    request_id = request(market)
    if stage == "ack":
        market.finish_external_close(request_id, 1, 200, 99.)
    before = snapshot(market)
    with pytest.raises(ProtectionBlocked, match="market_event_budget_exhausted"):
        if stage == "request":
            request(market)
        elif stage == "finish":
            market.finish_external_close(request_id, 1, 200, 99.)
        else:
            market.acknowledge_external_close(request_id, 2, 300)
    assert snapshot(market) == before


@pytest.mark.parametrize("already_processed", [False, True])
def test_finish_cannot_precede_request_or_overwrite_processed_effect(already_processed):
    market = book()
    request_id = request(market)
    if already_processed:
        market.finish_external_close(request_id, 1, 200, 99.)
    before = snapshot(market)
    with pytest.raises(ValueError):
        market.finish_external_close(request_id, 2, 300 if already_processed else 99, 98.)
    assert snapshot(market) == before


def test_external_partial_reason_cannot_remove_scheduled_close_for_same_ticket():
    market = book()
    market.request("leg-0", 0, 100, 100., .01, "partial_target", entry=False)
    scheduled = market.closes["leg-0"]
    request_id = request(market, reason="partial_target")
    market.finish_external_close(request_id, 1, 200, 99.)
    assert market.acknowledge_external_close(request_id, 1, 200) is True
    assert market.closes["leg-0"] is scheduled
    assert market.due_closes(2, 10**12) == (scheduled,)
    assert market.pending


def test_generic_external_close_remains_rejected_without_mutation():
    market = book()
    before = snapshot(market)
    with pytest.raises(ValueError):
        market.request("leg-0", 0, 100, 100., .04, "control", entry=False,
                       response_owner="external_runtime")
    assert snapshot(market) == before
