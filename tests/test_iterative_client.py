from dataclasses import asdict, replace
from datetime import timedelta

import pytest

from research.dubai_iterative.client import ClientProfile
from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import oracle_simulate
from research.dubai_iterative.portfolio import reconstruct_portfolio
from research.execution_profile import execution_from_mapping, execution_to_scenario
from tests.test_iterative_market import market
from tests.test_iterative_protection import BASE, BASE_NS, path, policy, profile


def run(quotes, *, mode="snapshot_batch", strategy=None, fill_ms=0, price_ms=None,
        protection=None, market_profile=None):
    return simulate(path(quotes), strategy or policy(
        leg_count=3, volume_weights=(.04,) * 3, target_steps=(20., 20., 20.),
        entry_ladder_mode="adverse", entry_ladder_step=1.5),
        execution=ExecutionAssumptions(
            entry_fill_latency_ms=fill_ms,
            protection=protection or profile(), market=market_profile or market(),
            client=ClientProfile(ladder_decision_mode=mode, entry_price_delay_ms=price_ms)))


def test_shared_modify_wait_prevents_new_entry_observation():
    result = run([100., 100., 100., 96., 100., 100., 100.],
                 protection=profile(processing_delay_ms=3000, acknowledgement_delay_ms=1000))
    assert len(result.entries) == 1


def test_crossed_batch_keeps_its_snapshot_after_price_recovers():
    result = run([100., 100., 100., 100., 100., 96., 100., 100., 100., 100., 100.])
    assert len(result.entries) == 3
    queued = [row for row in result.client_events if row.kind == "queued" and row.operation == "entry"]
    assert queued[1].decision_index == queued[2].decision_index == 5
    assert result.entries[2].requested_ns >= result.entries[1].acknowledged_ns


def test_fresh_quote_does_not_inherit_batch_commitment():
    result = run([100., 100., 100., 100., 100., 96., 100., 100., 100., 100., 100.], mode="fresh_quote")
    assert len(result.entries) == 2


def test_price_clock_is_separate_from_fill_clock():
    strategy = policy(target_steps=(20.,))
    result = run([100., 99., 98., 98., 98., 98.], strategy=strategy, fill_ms=2000, price_ms=0)
    assert result.entries[0].tick_index == 2
    assert result.entries[0].entry_price == 100.
    assert result.entries[0].price_tick_index == 0


def test_native_stop_still_closes_during_shared_modify_wait():
    result = run([100., 100., 100., 69.] + [69.] * 10, strategy=policy(),
                 protection=profile(processing_delay_ms=3000, acknowledgement_delay_ms=4000))
    assert result.exits[0].tick_index == 3
    assert result.exits[0].reason == "initial_sl"
    assert result.blockers == ()
    rejected = next(row for row in result.protection_events if row.kind == "rejected")
    assert rejected.reason == "position_already_closed"
    assert result.client_events[-1].kind == "released"


def test_installed_target_runs_before_modify_response():
    result = run([100., 100., 100., 100., 101.] + [101.] * 12, strategy=policy(),
                 protection=profile(acknowledgement_delay_ms=10_000))
    assert result.exits[0].tick_index == 4
    assert result.exits[0].reason == "per_leg_target"
    assert result.blockers == ()
    assert result.last_tick_index == 13


def test_serial_resource_has_no_overlapping_started_operations():
    result = run([100., 100., 100., 100., 100., 96.] + [100.] * 20)
    active = None
    for row in result.client_events:
        assert row.decision_index <= row.tick_index
        assert row.decision_ns <= row.time_ns
        if row.kind == "started":
            assert active is None
            active = (row.operation, row.ticket)
        if row.kind == "released":
            assert active == (row.operation, row.ticket)
            active = None
    assert active is None


def test_unavailable_response_blocks_completion_without_erasing_native_exit():
    result = run([100., 100., 100., 69.], strategy=policy(),
                 protection=profile(processing_delay_ms=3000, acknowledgement_delay_ms=4000))
    assert len(result.exits) == 1
    assert "client_lifecycle_incomplete_at_data_end" in result.blockers
    assert result.pnl_eur is None


def test_client_fill_missing_retains_request_and_decision():
    result = run([100.], strategy=policy(), fill_ms=1000)
    assert result.entries == ()
    assert [row.kind for row in result.market_events] == ["entry_requested"]
    assert [row.kind for row in result.client_events] == ["queued", "started"]
    assert "entry_fill_quote_missing" in result.blockers


def test_committed_batch_keeps_provisional_stop_from_decision_quote():
    result = run([100., 100., 100., 100., 100., 96.] + [100.] * 20)
    third = next(row for row in result.protection_events if row.kind == "open" and row.ticket == "sim_ladder_3")
    assert third.sl == 66.
    assert result.entries[2].entry_price == 100.


def test_client_provider_close_waits_for_current_request_then_closes():
    tape = replace(path([100.] * 12), provider_events=(
        ProviderEvent(BASE + timedelta(seconds=1), "CLOSE_ALL", {}),))
    result = simulate(tape, policy(provider_management_mode="explicit_close_only"),
        execution=ExecutionAssumptions(entry_fill_latency_ms=2000, protection=profile(),
                                       market=market(), client=ClientProfile()))
    assert len(result.entries) == len(result.exits) == 1
    assert result.exits[0].reason == "provider_close"
    assert result.exits[0].tick_index >= 5
    assert result.blockers == ()


def test_entry_price_after_fill_is_rejected():
    result = run([100., 100.], strategy=policy(), fill_ms=0, price_ms=100)
    assert result.entries == ()
    assert "client_price_clock_after_fill" in result.blockers


def test_typed_profile_survives_json_and_oracle_conversion():
    execution = ExecutionAssumptions(protection=profile(), market=market(), client=ClientProfile())
    assert execution_from_mapping(asdict(execution)) == execution
    assert execution_to_scenario(execution).client == execution.client


@pytest.mark.parametrize("change", [
    {"name": "observed"}, {"entry_price_delay_ms": True}, {"entry_price_delay_ms": -1},
    {"entry_price_delay_ms": 1.5}, {"max_events": True}, {"max_events": 0},
    {"ladder_decision_mode": "unknown"},
])
def test_client_profile_is_explicit_and_bounded(change):
    with pytest.raises(ValueError):
        ClientProfile(**change)


def test_unimplemented_backends_never_ignore_client_assumptions():
    execution = ExecutionAssumptions(protection=profile(), market=market(), client=ClientProfile())
    tape, strategy = path([100., 100., 101.]), policy()
    fast = FastEvaluator(execution=execution)(tape, strategy)
    oracle = oracle_simulate(tape, strategy, execution=execution_to_scenario(execution))
    assert "client_model_not_validated_in_fast" in fast.blockers
    assert "client_model_not_validated_in_oracle" in oracle.blockers
    assessment = reconstruct_portfolio((tape,), (fast,), execution=execution)
    assert "client_portfolio_execution_unvalidated" in assessment.blockers


def test_client_trace_budget_fails_closed():
    execution = ExecutionAssumptions(protection=profile(), market=market(), client=ClientProfile(max_events=1))
    result = simulate(path([100., 100., 101.]), policy(), execution=execution)
    assert result.entries == result.exits == ()
    assert "client_event_budget_exhausted" in result.blockers
    assert len(result.client_events) == 1


def test_future_quotes_do_not_change_earlier_decisions():
    left = run([100., 100., 100., 100., 100., 96., 100., 100., 100., 100.])
    right = run([100., 100., 100., 100., 100., 96., 99., 97., 95., 90.])
    prefix = lambda result: [row for row in result.client_events if row.tick_index <= 5]
    assert prefix(left) == prefix(right)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_batch_decision_and_execution_are_symmetric(direction):
    quotes = [100., 100., 100., 100., 100., 96.] + [100.] * 20
    if direction == "SELL":
        quotes = [200 - quote for quote in quotes]
    result = simulate(path(quotes, direction=direction), policy(
        leg_count=3, volume_weights=(.04,) * 3, target_steps=(20.,) * 3,
        entry_ladder_mode="adverse", entry_ladder_step=1.5),
        execution=ExecutionAssumptions(protection=profile(), market=market(), client=ClientProfile()))
    assert len(result.entries) == 3
    queued = [row for row in result.client_events if row.kind == "queued" and row.operation == "entry"]
    assert [row.decision_index for row in queued] == [0, 5, 5]


@pytest.mark.parametrize("mode, expected", [("snapshot_batch", 3), ("fresh_quote", 2)])
def test_snapshot_commitment_survives_entry_expiry(mode, expected):
    tape = path([100., 100., 100., 100., 100., 96.] + [100.] * 10,
                offsets=[0, 1, 2, 3, 4, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69])
    result = simulate(tape, policy(
        leg_count=3, volume_weights=(.04,) * 3, target_steps=(20.,) * 3,
        entry_ladder_mode="adverse", entry_ladder_step=1.5, entry_expiry_min=1),
        execution=ExecutionAssumptions(protection=profile(), market=market(),
                                       client=ClientProfile(ladder_decision_mode=mode)))
    assert len(result.entries) == expected
    if expected == 3:
        assert result.entries[2].requested_ns > BASE_NS + 60_000_000_000


def test_duplicate_quote_times_retain_snapshot_ordinal():
    tape = path([100., 100., 100., 100., 100., 96., 100., 100., 100., 100.],
                offsets=[0, 1, 2, 3, 4, 5, 5, 7, 8, 9])
    result = simulate(tape, policy(
        leg_count=3, volume_weights=(.04,) * 3, target_steps=(20.,) * 3,
        entry_ladder_mode="adverse", entry_ladder_step=1.5),
        execution=ExecutionAssumptions(protection=profile(), market=market(), client=ClientProfile()))
    assert len(result.entries) == 3
    queued = [row for row in result.client_events if row.kind == "queued" and row.operation == "entry"]
    assert [row.decision_index for row in queued] == [0, 5, 5]
    assert result.entries[1].entry_price == 96.


def test_multiple_close_requests_share_resource_and_keep_native_stops_active():
    tape = path([100., 100., 100., 100., 100., 98., 98., 98., 98., 98., 69.] + [69.] * 10)
    tape = replace(tape, provider_events=(ProviderEvent(BASE + timedelta(seconds=8), "CLOSE_ALL", {}),))
    result = simulate(tape, policy(
        leg_count=2, volume_weights=(.04,) * 2, target_steps=(20.,) * 2,
        entry_ladder_mode="adverse", entry_ladder_step=1.5,
        provider_management_mode="explicit_close_only"),
        execution=ExecutionAssumptions(protection=profile(), market=market(close_processing_delay_ms=3000),
                                       client=ClientProfile()))
    assert len(result.entries) == len(result.exits) == 2
    assert result.blockers == ()
    events = [row for row in result.market_events if row.kind == "close_requested"]
    assert len(events) == 2
    assert events[1].timestamp_ns > events[0].timestamp_ns
    assert any(row.reason == "trailing_stop" and row.tick_index == 10 for row in result.exits)
