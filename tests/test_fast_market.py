"""Synthetic contracts for the independently compiled market lifecycle."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import numpy as np
import pytest

from research.dubai_iterative.dataset import ProviderEvent, RolloverEvent
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator, _market_kernel
from research.dubai_iterative.market_contract import MarketProfile
from tests.test_iterative_protection import BASE, BASE_NS, path, policy, profile


def market(**changes):
    return replace(MarketProfile(2000, 1000, 1000, .01, 1., .01), **changes)


def compare(tape, *, strategy=None, market_profile=None, protection=None, fill_ms=0):
    strategy = strategy or policy()
    execution = ExecutionAssumptions(
        market=market_profile or market(), protection=protection or profile(),
        entry_fill_latency_ms=fill_ms,
    )
    fast = FastEvaluator(execution=execution)(tape, strategy)
    scalar = simulate(tape, strategy, execution=execution)
    assert fast == scalar
    assert _market_kernel.nopython_signatures
    return fast


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_entry_ack_gates_management(direction):
    quotes = [100., 100., 100., 100., 101.]
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    result = compare(path(quotes, direction=direction))
    assert result.entries[0].acknowledged_ns == BASE_NS + 2_000_000_000
    assert next(row.tick_index for row in result.protection_events if row.kind == "requested") == 2
    assert result.exits[0].tick_index == 4


def test_passive_close_before_ack_keeps_replay_alive():
    result = compare(path([100., 69., 69., 69.]))
    assert result.exits[0].tick_index == 1
    assert result.last_tick_index == 2
    assert result.entries[0].acknowledged_ns == BASE_NS + 2_000_000_000


def test_ladder_waits_for_actual_ack_quote():
    strategy = policy(
        leg_count=2, volume_weights=(.04, .04), target_steps=(.5, 1.),
        entry_ladder_mode="adverse", entry_ladder_step=1.5,
    )
    result = compare(path([100., 98., 100., 100.]), strategy=strategy)
    assert len(result.entries) == 1


def test_ladder_request_uses_strictly_later_quote_than_ack():
    strategy = policy(
        leg_count=2, volume_weights=(.04, .04), target_steps=(.5, 1.),
        entry_ladder_mode="adverse", entry_ladder_step=1.5,
    )
    result = compare(path([100., 98., 98., 98., 98.]), strategy=strategy)
    events = [row for row in result.market_events if row.tick_index == 2]
    assert [row.kind for row in events] == ["entry_acknowledged"]
    requests = [row for row in result.market_events if row.kind == "entry_requested"]
    assert requests[1].tick_index == 3


@pytest.mark.parametrize("rejection", ["invalid_volume", "invalid_initial_protection"])
def test_rejected_entry_is_a_completed_nonfill(rejection):
    if rejection == "invalid_volume":
        result = compare(path([100., 100., 100.]), market_profile=market(volume_min=.05))
    else:
        result = compare(path([100., 60., 60., 60.]), fill_ms=1000)
    assert result.entries == result.exits == ()
    assert result.exit_reason == "entry_rejected"
    assert result.unfilled and result.pnl_eur == Decimal("0.00")
    assert next(row for row in result.market_events if row.kind == "entry_rejected").reason == rejection


def close_tape(quotes, *, offsets=None, at=2):
    tape = path(quotes, offsets=offsets)
    return replace(tape, provider_events=(ProviderEvent(BASE + timedelta(seconds=at), "CLOSE_ALL", {}),))


def close_policy(**changes):
    return policy(target_mode="none", target_steps=(), provider_management_mode="explicit_close_only", **changes)


def test_close_processing_and_ack_have_separate_quote_deadlines():
    result = compare(
        close_tape([100., 100., 100., 99., 99.]), strategy=close_policy(),
        market_profile=market(entry_acknowledgement_delay_ms=0),
    )
    assert result.exits[0].tick_index == 3
    assert result.exits[0].exit_price == 98.8
    assert result.market_events[-1].tick_index == 4


@pytest.mark.parametrize("provider_second", [1, 2])
def test_closes_process_in_request_order_when_sparse_quotes_merge_deadlines(provider_second):
    tape = close_tape([100., 99., 99., 99., 99.], offsets=[0, 1, 2, 10, 11], at=provider_second)
    strategy = close_policy(leg_count=2, volume_weights=(.01, .04), hard_stop_eur_per_leg=3.)
    result = compare(
        tape, strategy=strategy, protection=profile(processing_delay_ms=10_000),
        market_profile=market(entry_acknowledgement_delay_ms=0, close_processing_delay_ms=2000),
    )
    assert [row.ticket for row in result.exits] == ["sim_2", "sim_1"]
    requested = [row for row in result.market_events if row.kind == "close_requested"]
    filled = [row for row in result.market_events if row.kind == "close_filled"]
    assert [row.request_id for row in requested] == [3, 4]
    assert [row.request_id for row in filled] == [3, 4]


def test_passive_stop_rejects_pending_close_without_double_money():
    result = compare(
        close_tape([100., 100., 100., 69., 69.]), strategy=close_policy(),
        market_profile=market(entry_acknowledgement_delay_ms=0),
        protection=profile(processing_delay_ms=10_000),
    )
    assert len(result.exits) == 1
    assert result.exits[0].reason == "initial_sl"
    assert result.pnl_eur == result.exits[0].pnl_eur
    assert any(row.kind == "close_rejected" and row.reason == "position_already_closed" for row in result.market_events)


def test_data_end_cannot_publish_a_future_ack():
    result = compare(path([100., 69.]))
    assert result.entries[0].acknowledged_ns is None
    assert len(result.exits) == 1 and result.pnl_eur is None
    assert "market_lifecycle_incomplete_at_data_end" in result.blockers


@pytest.mark.parametrize("leg_count", [1, 2])
def test_request_without_a_future_fill_keeps_typed_request_trace(leg_count):
    result = compare(
        path([100.]), fill_ms=1000,
        strategy=policy(leg_count=leg_count, volume_weights=(.04,) * leg_count,
                        target_steps=(.5,) * leg_count),
    )
    assert len(result.market_events) == leg_count
    assert all(row.kind == "entry_requested" for row in result.market_events)
    assert result.entries == () and result.pnl_eur is None
    assert "entry_fill_quote_missing" in result.blockers


@pytest.mark.parametrize("future_fill", [False, True])
@pytest.mark.parametrize("first_invalid", [False, True])
@pytest.mark.parametrize("ladder", [False, True])
def test_missing_first_fill_preserves_duplicate_timestamp_request_ordinal(future_fill, first_invalid, ladder):
    quotes = [np.nan, 100.] if first_invalid else [100., 101.]
    offsets = [0, 0]
    if future_fill:
        quotes.append(quotes[-1])
        offsets.append(1)
    changes = {} if first_invalid else {"entry_mode": "momentum", "entry_value": .5}
    if ladder:
        changes.update(leg_count=2, volume_weights=(.04, .04), target_steps=(.5, 1.),
                       entry_ladder_mode="adverse", entry_ladder_step=1.5)
    tape, strategy = path(quotes, offsets=offsets), policy(**changes)
    execution = ExecutionAssumptions(market=market(), protection=profile(), entry_fill_latency_ms=1000)
    result = FastEvaluator(execution=execution)(tape, strategy)
    assert result.entries == result.exits == ()
    assert len(result.market_events) == 1
    request = result.market_events[0]
    assert request.kind == "entry_requested" and request.tick_index == 1
    assert request.price == quotes[1] and request.timestamp_ns == BASE_NS
    if future_fill:
        assert result.blockers == ("protection_request_quote_ambiguous:sim_1",)
    else:
        assert "entry_fill_quote_missing" in result.blockers
    assert result == simulate(tape, strategy, execution=execution)


@pytest.mark.parametrize("future_fill", [False, True])
def test_missing_later_fill_preserves_duplicate_timestamp_request_ordinal(future_fill):
    quotes, offsets = [100., 100., 99., 98.], [0, 1, 2, 2]
    if future_fill:
        quotes.append(98.)
        offsets.append(3)
    tape = path(quotes, offsets=offsets)
    strategy = policy(leg_count=2, volume_weights=(.04, .04), target_steps=(.5, 1.),
                      entry_ladder_mode="adverse", entry_ladder_step=1.5)
    execution = ExecutionAssumptions(market=market(entry_acknowledgement_delay_ms=0),
                                     protection=profile(), entry_fill_latency_ms=1000)
    result = FastEvaluator(execution=execution)(tape, strategy)
    requests = [row for row in result.market_events if row.kind == "entry_requested"]
    assert len(requests) == 2 and requests[1].tick_index == 3
    assert requests[1].price == 98. and requests[1].timestamp_ns == BASE_NS + 2_000_000_000
    assert len(result.entries) == 1
    if future_fill:
        assert "protection_request_quote_ambiguous:sim_ladder_2" in result.blockers
    else:
        assert "entry_fill_quote_missing" in result.blockers
    assert result == simulate(tape, strategy, execution=execution)


@pytest.mark.parametrize("quotes", [
    [100., 100., 100., 102., 101., 98., 98., 98.],
    [100., 100., 100., 102., 101., 98.],
])
def test_cancelled_unrequested_ladder_does_not_create_pending_diagnostics(quotes):
    result = compare(
        path(quotes), fill_ms=2000,
        strategy=policy(leg_count=2, volume_weights=(.04, .04), target_mode="none", target_steps=(),
                        entry_ladder_mode="adverse", entry_ladder_step=1.5,
                        profit_lock_arm=4., profit_lock_giveback=1.),
        market_profile=market(entry_acknowledgement_delay_ms=0, close_processing_delay_ms=0,
                              close_acknowledgement_delay_ms=0),
        protection=profile(processing_delay_ms=10_000),
    )
    assert len(result.entries) == len(result.exits) == 1
    assert result.exits[0].reason == "profit_lock" and result.exits[0].tick_index == 5
    assert not any(row.ticket == "sim_ladder_2" for row in result.market_events)
    assert result.blockers == () and result.pnl_eur == result.exits[0].pnl_eur


def test_later_missing_fill_keeps_the_earlier_passive_exit():
    result = compare(
        path([100., 100., 100., 101., 98.]), fill_ms=1000,
        market_profile=market(entry_acknowledgement_delay_ms=0),
        strategy=policy(leg_count=2, volume_weights=(.04, .04), target_steps=(.5, 1.),
                        entry_ladder_mode="adverse", entry_ladder_step=1.5),
    )
    assert len(result.entries) == len(result.exits) == 1
    assert result.market_events[-1].kind == "entry_requested"
    assert "entry_fill_quote_missing" in result.blockers


def test_missing_first_fill_and_exhausted_batch_trace_keep_both_limitations():
    result = compare(
        path([100., 100.]), fill_ms=10_000,
        strategy=policy(leg_count=2, volume_weights=(.04, .04), target_steps=(.5, 1.)),
        market_profile=market(max_events=1),
    )
    assert len(result.market_events) == 1 and result.last_tick_index == 0
    assert result.blockers == (
        "market_event_budget_exhausted", "entry_fill_quote_missing",
    )


@pytest.mark.parametrize("quotes", [[100., 100., 100.], [100., 100.]])
def test_provider_close_during_first_pending_fill_keeps_request_prefix(quotes):
    result = compare(close_tape(quotes, at=1), strategy=close_policy(), fill_ms=2000)
    assert result.entries == result.exits == ()
    assert [row.kind for row in result.market_events] == ["entry_requested"]
    assert "market_close_with_entry_in_flight_unsupported" in result.blockers
    assert result.last_tick_index == 1 and result.pnl_eur is None


def test_fill_event_budget_does_not_create_an_unrecorded_position():
    result = compare(path([100., 100., 100.]), market_profile=market(max_events=1))
    assert result.entries == result.exits == ()
    assert [row.kind for row in result.market_events] == ["entry_requested"]
    assert result.protection_events == ()
    assert result.exit_reason == "blocked"
    assert "market_event_budget_exhausted" in result.blockers


def test_no_pending_plan_cannot_reopen_while_waiting_for_ack():
    result = compare(
        path([100., 69., 69., 69., 38., 38.]),
        strategy=policy(leg_count=2, volume_weights=(.04, .04), target_steps=(.5, 1.),
                        entry_ladder_mode="adverse", entry_ladder_step=1.5,
                        pending_entry_policy="none"),
    )
    assert len(result.entries) == 1
    assert result.entries[0].acknowledged_ns == BASE_NS + 2_000_000_000


def test_flat_client_never_acknowledges_on_invalid_quote():
    result = compare(path([100., 69., np.nan]))
    assert result.entries[0].acknowledged_ns is None
    assert "market_lifecycle_incomplete_at_data_end" in result.blockers


def test_exhausted_ack_budget_cannot_hide_old_installed_stop():
    result = compare(path([100., 100., 69.]), market_profile=market(max_events=2))
    assert len(result.exits) == 1 and result.exits[0].tick_index == 2
    assert result.exits[0].reason == "initial_sl"
    assert result.entries[0].acknowledged_ns is None
    assert "market_event_budget_exhausted" in result.blockers


def test_zero_delay_ladder_rejection_cancels_unrequested_third_leg():
    result = compare(
        path([100., 96., 65., 65.]),
        strategy=policy(leg_count=3, volume_weights=(.04, .01, .04), target_steps=(.5, 1., 1.5),
                        entry_ladder_mode="adverse", entry_ladder_step=1.5),
        market_profile=market(entry_acknowledgement_delay_ms=0, volume_min=.02),
    )
    assert len(result.entries) == 1
    assert not any(row.ticket == "sim_ladder_3" for row in result.market_events)


def test_simultaneous_batch_keeps_requests_already_sent_before_rejection():
    result = compare(
        path([100., 100., 101.]),
        strategy=policy(leg_count=2, volume_weights=(.01, .04), target_steps=(.5, .5)),
        market_profile=market(entry_acknowledgement_delay_ms=0, volume_min=.02),
    )
    assert [row.ticket for row in result.entries] == ["sim_2"]
    assert len(result.exits) == 1 and result.blockers == ()
    assert [row.request_id for row in result.market_events[:2]] == [1, 2]


def test_rejection_event_budget_keeps_request_without_raising():
    result = compare(path([100., 60.]), fill_ms=1000, market_profile=market(max_events=1))
    assert result.entries == result.exits == ()
    assert [row.kind for row in result.market_events] == ["entry_requested"]


@pytest.mark.parametrize("orientation", ["identity", "account_base_profit_quote", "profit_base_account_quote"])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_market_money_uses_existing_fixed_point_conversion_and_swap(orientation, direction):
    quotes = [100., 100., 100., 99., 99.]
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    tape = path(quotes, direction=direction)
    tape = replace(
        tape, conversion_orientation=orientation,
        fx_bid=np.full(5, 1.08), fx_ask=np.full(5, 1.09),
        provider_events=(ProviderEvent(BASE + timedelta(seconds=2), "CLOSE_ALL", {}),),
        rollover_events=(RolloverEvent(
            observed_at=BASE + timedelta(seconds=1),
            minor_by_volume_unit=np.asarray([0, -1, -2, -3, -4], dtype=np.int64),
            blocker=None,
        ),),
    )
    result = compare(tape, strategy=close_policy(), market_profile=market(entry_acknowledgement_delay_ms=0))
    assert len(result.exits) == 1
    assert result.pnl_eur == result.exits[0].pnl_eur


def test_subcent_hard_stop_remains_enabled_after_money_rounding():
    result = compare(
        path([100., 100., 100.]),
        strategy=policy(target_mode="none", target_steps=(), hard_stop_eur_per_leg=.001),
        market_profile=market(entry_acknowledgement_delay_ms=0),
    )
    assert result.exits[0].reason == "hard_stop_per_leg"


def test_close_zero_latency_requires_later_index_even_equal_timestamp():
    result = compare(
        close_tape([100., 100., 99., 99.], offsets=[0, 1, 1, 2], at=1),
        strategy=close_policy(),
        market_profile=market(entry_acknowledgement_delay_ms=0, close_processing_delay_ms=0, close_acknowledgement_delay_ms=0),
    )
    request = next(row for row in result.market_events if row.kind == "close_requested")
    fill = next(row for row in result.market_events if row.kind == "close_filled")
    assert request.tick_index == 1 and fill.tick_index == 2
    assert request.timestamp_ns == fill.timestamp_ns


@pytest.mark.parametrize("budget", [1, 2, 3, 4, 5])
def test_market_trace_exhaustion_preserves_completed_prefix(budget):
    result = compare(
        close_tape([100., 100., 100., 99., 99.]), strategy=close_policy(),
        market_profile=market(entry_acknowledgement_delay_ms=0, max_events=budget),
    )
    assert len(result.market_events) == budget
    assert "market_event_budget_exhausted" in result.blockers
    assert result.pnl_eur is None


def test_close_event_budget_does_not_create_an_unrecorded_exit():
    result = compare(
        close_tape([100., 100., 100., 99., 99.]), strategy=close_policy(),
        market_profile=market(entry_acknowledgement_delay_ms=0, max_events=4),
    )
    assert len(result.entries) == 1 and result.exits == ()
    assert result.exit_reason == "not_closed"
    assert result.market_events[-1].kind == "close_requested"
    assert not any(row.kind == "closed" for row in result.protection_events)
    assert result.blockers == ("market_event_budget_exhausted",)
    assert result.pnl_eur is None


def test_profit_lock_does_not_arm_from_unacknowledged_entry_peak():
    result = compare(
        path([100., 110., 100., 100., 100.]),
        strategy=policy(target_mode="none", target_steps=(), profit_lock_arm=30., profit_lock_giveback=1.),
    )
    assert result.max_favourable_eur > Decimal("30")
    assert not any(row.kind == "close_requested" for row in result.market_events)


@pytest.mark.parametrize("reason", ["hard_stop_per_leg", "profit_lock", "time_exit"])
def test_policy_risk_requests_delayed_market_close(reason):
    changes = {"target_mode": "none", "target_steps": ()}
    quotes, offsets = [100., 110., 105., 104., 104.], None
    if reason == "hard_stop_per_leg":
        changes["hard_stop_eur_per_leg"] = 1.
        quotes = [100., 99., 99., 99., 99.]
    elif reason == "profit_lock":
        changes.update(profit_lock_arm=30., profit_lock_giveback=1.)
    else:
        changes.update(time_exit_min=1, time_exit_mode="profit_only")
        offsets = [0, 60, 61, 62, 63]
    result = compare(
        path(quotes, offsets=offsets), strategy=policy(**changes),
        market_profile=market(entry_acknowledgement_delay_ms=0),
    )
    assert result.exits[0].reason == reason
    requested = next(row for row in result.market_events if row.kind == "close_requested")
    assert result.exits[0].tick_index > requested.tick_index


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("seed", range(12))
def test_seeded_compiled_lifecycle_matches_scalar(seed, direction):
    rng = np.random.default_rng(seed)
    quotes = (10000 + np.r_[0, np.cumsum(rng.choice([-250, -100, -30, 0, 30, 100, 250], 29))]) / 100
    if direction == "SELL":
        quotes = 200 - quotes
    strategy = policy(
        leg_count=2, volume_weights=(.04, .03), target_steps=(.5, 1.),
        entry_ladder_mode="adverse", entry_ladder_step=1.5, trailing_distance=2.,
    )
    compare(
        path(quotes, direction=direction, offsets=np.arange(len(quotes)) / 5),
        strategy=strategy, fill_ms=[0, 200][seed % 2],
        protection=profile(processing_delay_ms=[0, 200, 1000][seed % 3]),
        market_profile=market(
            entry_acknowledgement_delay_ms=[0, 250][seed % 2],
            close_processing_delay_ms=[0, 200, 1000][seed % 3],
        ),
    )


@pytest.mark.parametrize("seed", [0, 1, 7, 25, 37, 40, 50, 62, 71, 74, 76, 77])
def test_seeded_market_risk_and_raw_request_prices_match(seed):
    rng = np.random.default_rng(seed)
    quotes = (10000 + np.r_[0, np.cumsum(rng.choice([-100, -30, 0, 30, 100], 39))]) / 100
    direction = "BUY" if seed % 2 else "SELL"
    if direction == "SELL":
        quotes = 200 - quotes
    tape = path(quotes, direction=direction, offsets=np.arange(40) / 5)
    tape = replace(tape, provider_events=(ProviderEvent(BASE + timedelta(seconds=4), "CLOSE_ALL", {}),))
    mode = "adverse" if seed % 3 else "simultaneous"
    strategy = policy(
        leg_count=2, volume_weights=(.04, .03), target_steps=(.5, 1.), trailing_distance=2.,
        entry_ladder_mode=mode, entry_ladder_step=1.5 if mode == "adverse" else None,
        hard_stop_eur_per_leg=3. if seed % 2 else None,
        profit_lock_arm=2. if seed % 3 else None, profit_lock_giveback=1. if seed % 3 else None,
        provider_management_mode="explicit_close_only",
    )
    compare(
        tape, strategy=strategy, fill_ms=[0, 200][seed % 2],
        protection=profile(processing_delay_ms=[0, 200, 1000][seed % 3]),
        market_profile=market(entry_acknowledgement_delay_ms=[0, 200, 500][seed % 3],
                              close_processing_delay_ms=[0, 500][seed % 2],
                              close_acknowledgement_delay_ms=200),
    )
