from dataclasses import replace
from datetime import timedelta

import pytest

from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.market_contract import MarketProfile
from tests.test_iterative_protection import BASE, BASE_NS, path, policy, profile


def market(**changes):
    return replace(MarketProfile(2000, 1000, 1000, .01, 1., .01), **changes)


def run(tape, *, strategy=None, market_profile=None, fill_ms=0, protection=None):
    return simulate(tape, strategy or policy(), execution=ExecutionAssumptions(
        entry_fill_latency_ms=fill_ms, protection=protection or profile(),
        market=market_profile or market()))


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_management_does_not_use_entry_before_ack(direction):
    quotes = [100., 100., 100., 100., 101.]
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    result = run(path(quotes, direction=direction))
    assert result.entries[0].acknowledged_ns == BASE_NS + 2_000_000_000
    assert next(row.tick_index for row in result.protection_events if row.kind == "requested") == 2
    assert result.exits[0].tick_index == 4
    assert result.blockers == ()


def test_installed_stop_can_close_before_open_ack():
    result = run(path([100., 69., 69., 69.]))
    assert result.exits[0].tick_index == 1
    assert result.entries[0].acknowledged_ns == BASE_NS + 2_000_000_000
    assert [event.kind for event in result.market_events] == [
        "entry_requested", "entry_filled", "entry_acknowledged"]
    assert result.blockers == ()


def test_ladder_cannot_use_fill_price_before_ack():
    strategy = policy(leg_count=2, volume_weights=(.04, .04), target_steps=(.5, 1.),
                      entry_ladder_mode="adverse", entry_ladder_step=1.5)
    result = run(path([100., 98., 100., 100.]), strategy=strategy)
    assert len(result.entries) == 1


def test_invalid_initial_stop_rejects_entry_without_inventing_fill():
    result = run(path([100., 60., 60., 60.]), fill_ms=1000)
    assert result.entries == () and result.exits == ()
    rejects = [event for event in result.market_events if event.kind == "entry_rejected"]
    assert rejects[0].reason == "invalid_initial_protection"
    assert rejects[0].tick_index == 1
    assert result.blockers == ()


def test_invalid_volume_is_a_rejection_not_a_fill():
    result = run(path([100., 100., 100.]), market_profile=market(volume_min=.05))
    assert result.entries == ()
    assert any(row.reason == "invalid_volume" for row in result.market_events)
    assert result.blockers == ()


def close_tape(quotes):
    tape = path(quotes)
    return replace(tape, provider_events=(ProviderEvent(BASE + timedelta(seconds=2), "CLOSE_ALL", {}),))


def test_market_close_waits_for_processing_quote_and_ack():
    result = run(close_tape([100., 100., 100., 99., 99.]),
                 strategy=policy(target_mode="none", target_steps=(), provider_management_mode="explicit_close_only"),
                 market_profile=market(entry_acknowledgement_delay_ms=0))
    assert result.exits[0].tick_index == 3 and result.exits[0].exit_price == 98.8
    assert result.market_events[-1].kind == "close_acknowledged"
    assert result.market_events[-1].tick_index == 4 and result.blockers == ()


def test_installed_stop_wins_over_pending_market_close():
    result = run(close_tape([100., 100., 100., 69., 69.]),
                 strategy=policy(target_mode="none", target_steps=(), provider_management_mode="explicit_close_only"),
                 market_profile=market(entry_acknowledgement_delay_ms=0),
                 protection=profile(processing_delay_ms=10_000))
    assert len(result.exits) == 1 and result.exits[0].reason == "initial_sl"
    assert any(row.kind == "close_rejected" and row.reason == "position_already_closed" for row in result.market_events)
    assert result.blockers == ()


def test_data_end_before_ack_keeps_known_exit_but_blocks_completion():
    result = run(path([100., 69.]))
    assert result.exits[0].tick_index == 1 and result.entries[0].acknowledged_ns is None
    assert "market_lifecycle_incomplete_at_data_end" in result.blockers
    assert result.pnl_eur is None


def test_first_request_without_a_fill_keeps_the_request_trace():
    result = run(path([100.]), fill_ms=1000)
    assert result.entries == () and result.exits == ()
    assert [event.kind for event in result.market_events] == ["entry_requested"]
    assert "entry_fill_quote_missing" in result.blockers
    assert "market_lifecycle_incomplete_at_data_end" in result.blockers


def test_rejection_cannot_escape_when_trace_budget_is_exhausted():
    result = run(path([100., 60.]), fill_ms=1000, market_profile=market(max_events=1))
    assert result.entries == () and result.exits == ()
    assert [event.kind for event in result.market_events] == ["entry_requested"]
    assert "market_event_budget_exhausted" in result.blockers


def test_zero_delay_ladder_rejection_cancels_later_unrequested_leg():
    strategy = policy(leg_count=3, volume_weights=(.04, .01, .04),
                      target_steps=(.5, 1., 1.5), entry_ladder_mode="adverse",
                      entry_ladder_step=1.5)
    result = run(path([100., 96., 65., 65.]), strategy=strategy,
                 market_profile=market(entry_acknowledgement_delay_ms=0, volume_min=.02))
    assert len(result.entries) == 1
    assert not any(row.ticket == "sim_ladder_3" for row in result.market_events)
    assert any(row.reason == "invalid_volume" for row in result.market_events)


def test_flat_ack_wait_does_not_reactivate_cancelled_ladder():
    strategy = policy(leg_count=2, volume_weights=(.04, .04), target_steps=(.5, 1.),
                      entry_ladder_mode="adverse", entry_ladder_step=1.5,
                      pending_entry_policy="none")
    result = run(path([100., 69., 69., 69., 38., 38.]), strategy=strategy)
    assert len(result.entries) == 1 and result.exits[0].tick_index == 1
    assert result.entries[0].acknowledged_ns == BASE_NS + 2_000_000_000
    assert result.blockers == ()


def test_flat_pending_ack_never_uses_invalid_quote():
    result = run(path([100., 69., float("nan")]))
    assert len(result.exits) == 1 and result.exits[0].tick_index == 1
    assert result.entries[0].acknowledged_ns is None
    assert not any(row.kind == "entry_acknowledged" for row in result.market_events)
    assert "market_lifecycle_incomplete_at_data_end" in result.blockers


def test_ack_budget_failure_preserves_passive_exit_on_same_quote():
    result = run(path([100., 100., 69.]), market_profile=market(max_events=2))
    assert len(result.exits) == 1 and result.exits[0].tick_index == 2
    assert result.exits[0].reason == "initial_sl"
    assert "market_event_budget_exhausted" in result.blockers


@pytest.mark.parametrize("quotes", [[100., 100., 100.], [100., 100.]])
def test_provider_close_during_first_pending_fill_keeps_request_prefix(quotes):
    tape = replace(path(quotes), provider_events=(
        ProviderEvent(BASE + timedelta(seconds=1), "CLOSE_ALL", {}),))
    result = run(tape, strategy=policy(provider_management_mode="explicit_close_only"), fill_ms=2000)
    assert result.entries == result.exits == ()
    assert [row.kind for row in result.market_events] == ["entry_requested"]
    assert "market_close_with_entry_in_flight_unsupported" in result.blockers
    assert result.last_tick_index == 1 and result.pnl_eur is None


def test_fill_event_budget_does_not_create_an_unrecorded_position():
    result = run(path([100., 100., 100.]), market_profile=market(max_events=1))
    assert result.entries == result.exits == ()
    assert [row.kind for row in result.market_events] == ["entry_requested"]
    assert result.protection_events == ()
    assert "market_event_budget_exhausted" in result.blockers


def test_close_event_budget_does_not_create_an_unrecorded_exit():
    result = run(close_tape([100., 100., 100., 99., 99.]),
                 strategy=policy(target_mode="none", target_steps=(), provider_management_mode="explicit_close_only"),
                 market_profile=market(entry_acknowledgement_delay_ms=0, max_events=4))
    assert len(result.entries) == 1 and result.exits == ()
    assert result.market_events[-1].kind == "close_requested"
    assert not any(row.kind == "closed" for row in result.protection_events)
    assert "market_event_budget_exhausted" in result.blockers


def test_missing_fill_request_preserves_original_quote_ordinal():
    strategy = policy(entry_mode="momentum", entry_value=.5)
    short = run(path([100., 101.], offsets=[0, 0]), strategy=strategy, fill_ms=1000)
    extended = run(path([100., 101., 101.], offsets=[0, 0, 1]), strategy=strategy, fill_ms=1000)
    assert short.market_events[0] == extended.market_events[0]
    assert short.market_events[0].tick_index == 1
    assert short.market_events[0].price == 101.


def test_missing_fill_cannot_relocate_request_to_invalid_equal_timestamp_quote():
    result = run(path([float("nan"), 100.], offsets=[0, 0]), fill_ms=1000)
    assert result.market_events[0].tick_index == 1 and result.market_events[0].price == 100.


@pytest.mark.parametrize("quotes", [
    [100., 100., 100., 102., 101., 98., 98., 98.],
    [100., 100., 100., 102., 101., 98.],
])
def test_cancelled_unrequested_ladder_is_not_reported_as_in_flight(quotes):
    strategy = policy(leg_count=2, volume_weights=(.04, .04), target_mode="none", target_steps=(),
                      entry_ladder_mode="adverse", entry_ladder_step=1.5,
                      profit_lock_arm=4., profit_lock_giveback=1.)
    result = run(path(quotes), strategy=strategy, fill_ms=2000,
                 market_profile=market(entry_acknowledgement_delay_ms=0,
                                       close_processing_delay_ms=0, close_acknowledgement_delay_ms=0),
                 protection=profile(processing_delay_ms=10_000))
    assert len(result.entries) == len(result.exits) == 1
    assert result.exits[0].reason == "profit_lock" and result.exits[0].tick_index == 5
    assert not any(row.ticket == "sim_ladder_2" for row in result.market_events)
    assert result.blockers == ()
