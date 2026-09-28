from copy import deepcopy

from copy import deepcopy

import pytest

from research.dubai_iterative.market import MarketBook
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.protection import ProtectionBlocked


def book(*, delay=0, max_events=20):
    return MarketBook(MarketProfile(delay, 0, delay, .01, 1., .01, max_events=max_events))


def request(market, *, owner="external_runtime", entry=True, ticket="leg-0"):
    market.request(ticket, 0, 100, 100., .04, "entry_control", entry=entry,
                   response_owner=owner)


def snapshot(market):
    return deepcopy((market.requests, market.entries, market.closes, market.events))


@pytest.mark.parametrize("delay", [0, 250])
@pytest.mark.parametrize("rejection", [None, "invalid_initial_protection"])
def test_external_response_never_uses_timed_ack(delay, rejection):
    market = book(delay=delay)
    request(market)
    market.finish("leg-0", 1, 200, 101., entry=True, rejection=rejection)
    state = market.entries["leg-0"]
    assert state.response_owner == "external_runtime"
    assert state.processed_ns == 200 and state.acknowledgement_ns is None
    before = snapshot(market)
    for now in (200, 250_000_200, 10**12):
        assert market.acknowledge(2, now) == {}
        assert snapshot(market) == before
    assert market.entry_waiting and market.pending


@pytest.mark.parametrize("rejection", [None, "invalid_volume"])
@pytest.mark.parametrize("ack_ns", [200, 300])
def test_external_ack_records_actual_response_clock(rejection, ack_ns):
    market = book(delay=250)
    request(market)
    market.finish("leg-0", 1, 200, 101., entry=True, rejection=rejection)
    assert market.acknowledge_external("leg-0", 2, ack_ns) is True
    state = market.entries["leg-0"]
    assert state.acknowledged and state.acknowledgement_ns == ack_ns
    assert not market.pending and not market.entry_waiting
    event = market.events[-1]
    assert (event.ticket, event.request_id, event.kind) == ("leg-0", state.request_id, "entry_acknowledged")
    assert (event.tick_index, event.timestamp_ns) == (2, ack_ns)
    assert event.price == (None if rejection else 101.)
    assert event.reason == (rejection or "accepted")
    before = snapshot(market)
    assert market.acknowledge(3, ack_ns + 1) == {}
    assert snapshot(market) == before


@pytest.mark.parametrize("processed", [False, True])
def test_external_ack_before_fill_or_before_processing_clock_is_invalid(processed):
    market = book()
    request(market)
    if processed:
        market.finish("leg-0", 1, 200, 101., entry=True)
    before = snapshot(market)
    with pytest.raises(ValueError):
        market.acknowledge_external("leg-0", 1, 199)
    assert snapshot(market) == before


def test_duplicate_external_ack_is_noop_even_at_event_capacity():
    market = book(max_events=3)
    request(market)
    market.finish("leg-0", 1, 200, 101., entry=True)
    assert market.acknowledge_external("leg-0", 1, 200) is True
    before = snapshot(market)
    assert market.acknowledge_external("leg-0", 2, 300) is False
    assert snapshot(market) == before


def test_history_reconciliation_resolves_pending_without_inventing_native_ack():
    market = book(max_events=3)
    request(market)
    market.finish("leg-0", 1, 200, 101., entry=True)
    before = snapshot(market)
    with pytest.raises(ValueError):
        market.reconcile_external("leg-0", 1, 199)
    assert snapshot(market) == before
    assert market.reconcile_external("leg-0", 2, 300) is True
    state = market.entries["leg-0"]
    assert state.acknowledged and state.acknowledgement_ns is None
    assert not market.pending and not market.entry_waiting
    assert [(event.kind, event.timestamp_ns) for event in market.events] == [
        ("entry_requested", 100), ("entry_filled", 200), ("entry_reconciled", 300)
    ]
    before = snapshot(market)
    assert market.reconcile_external("leg-0", 3, 400) is False
    assert market.acknowledge_external("leg-0", 3, 400) is False
    assert snapshot(market) == before


@pytest.mark.parametrize("owner", ["unknown", None, True, []])
def test_invalid_response_owner_does_not_create_request(owner):
    market = book()
    before = snapshot(market)
    with pytest.raises(ValueError):
        request(market, owner=owner)
    assert snapshot(market) == before


def test_external_close_owner_is_not_admitted():
    market = book()
    with pytest.raises(ValueError):
        request(market, entry=False)
    assert not market.requests and not market.closes and not market.events


def test_duplicate_request_cannot_change_response_owner():
    market = book()
    request(market, owner="quote_clock")
    before = snapshot(market)
    with pytest.raises(ValueError):
        request(market)
    assert snapshot(market) == before


@pytest.mark.parametrize("entry", [False, True])
def test_quote_clock_request_cannot_be_externally_acknowledged(entry):
    market = book()
    request(market, owner="quote_clock", entry=entry)
    market.finish("leg-0", 1, 200, 101., entry=entry)
    before = snapshot(market)
    with pytest.raises(ValueError):
        market.acknowledge_external("leg-0", 1, 200, entry=entry)
    assert snapshot(market) == before


@pytest.mark.parametrize("stage", ["request", "finish", "ack"])
def test_event_capacity_is_checked_before_mutation(stage):
    market = book(max_events=2 if stage == "ack" else 1)
    request(market)
    if stage == "ack":
        market.finish("leg-0", 1, 200, 101., entry=True)
    before = snapshot(market)
    with pytest.raises(ProtectionBlocked, match="market_event_budget_exhausted"):
        if stage == "request":
            request(market, ticket="leg-1")
        elif stage == "finish":
            market.finish("leg-0", 1, 200, 101., entry=True)
        else:
            market.acknowledge_external("leg-0", 2, 300)
    assert snapshot(market) == before


@pytest.mark.parametrize("entry", [False, True])
@pytest.mark.parametrize("delay", [0, 250])
@pytest.mark.parametrize("rejection", [None, "rejected"])
def test_default_quote_clock_path_keeps_its_ack_contract(entry, delay, rejection):
    market = book(delay=delay)
    market.request("leg-0", 0, 100, 100., .04, "control", entry=entry)
    market.finish("leg-0", 1, 200, 101., entry=entry, rejection=rejection)
    due = 200 + delay * 1_000_000
    assert market.acknowledge(1, due - 1) == {}
    assert market.pending
    assert market.acknowledge(2, due) == ({"leg-0": due} if entry and not rejection else {})
    assert not market.pending
    assert market.events[-1].kind == ("entry_acknowledged" if entry else "close_acknowledged")


def test_default_partial_close_still_releases_ticket_for_next_request():
    market = book()
    market.request("leg-0", 0, 100, 100., .01, "partial_target", entry=False)
    market.finish("leg-0", 1, 200, 101., entry=False)
    assert market.acknowledge(1, 200) == {}
    assert "leg-0" not in market.closes
