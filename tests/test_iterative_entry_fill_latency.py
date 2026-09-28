from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import ProviderEvent, SignalLeg, SignalPath
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import ExecutionScenario, certify_candidate, oracle_simulate


BASE = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
BASE_NS = 1_788_861_600_000_000_000


def path(quotes, *, direction="BUY", offsets=None):
    offsets = list(range(len(quotes))) if offsets is None else offsets
    quote = np.array(quotes, dtype=float)
    bid = quote - 0.2 if direction == "BUY" else quote
    ask = quote if direction == "BUY" else quote + 0.2
    ones = np.ones(len(quotes))
    leg = SignalLeg("template", "market", 0.04, BASE, 1, None, None, None, Decimal(0), (), ())
    return SignalPath("control", "2026-09-08", direction, BASE, BASE, None, (leg,), (),
        np.array([BASE_NS + int(value * 1_000_000_000) for value in offsets], dtype=np.int64),
        bid, ask, bid if direction == "BUY" else ask, ones, ones, ones * 0,
        np.ones(len(quotes), dtype=bool), 100, "identity", 2, ({},), ({},), "provider_signal")


def genome(**changes):
    base = StrategyGenome(schema_version=2, entry_mode="adverse_reversal", entry_value=1.,
        entry_confirmation_value=1.5, leg_count=2, volume_weights=(0.04, 0.03),
        entry_ladder_mode="adverse", entry_ladder_step=1.5, entry_expiry_min=1,
        target_mode="none", stop_mode="none", be_mode="none", time_exit_min=180,
        provider_management_mode="ignore", pending_entry_policy="until_expiry")
    return base.with_change(**changes) if changes else base


def run(engine, tape, policy, delay=1000):
    if engine == "oracle":
        return oracle_simulate(tape, policy, execution=ExecutionScenario(entry_fill_latency_ms=delay))
    execution = ExecutionAssumptions(entry_fill_latency_ms=delay)
    return simulate(tape, policy, execution=execution) if engine == "scalar" else FastEvaluator(execution=execution)(tape, policy)


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_delayed_fill_reanchors_ladder_to_executed_quote_not_trigger(engine, direction):
    quotes = [100., 98., 99.5, 99., 98., 98.]
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    tape = path(quotes, direction=direction)
    instant = run(engine, tape, genome(), delay=0)
    delayed = run(engine, tape, genome())
    assert len(instant.entries) == 2
    assert len(delayed.entries) == 1
    assert delayed.entries[0].tick_index == 3
    assert delayed.entries[0].entry_price == quotes[3]
    assert delayed.entries[0].opened_at == BASE + timedelta(seconds=3)


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_subsequent_ladder_order_has_its_own_execution_delay(engine):
    tape = path([100., 100., 98.5, 98.2, 98.2])
    policy = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None)
    result = run(engine, tape, policy)
    assert [row.tick_index for row in result.entries] == [1, 3]
    assert [row.entry_price for row in result.entries] == [100., 98.2]


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_request_before_expiry_can_fill_after_expiry_without_new_orders(engine):
    tape = path([100., 100.], offsets=[0, 61])
    policy = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None)
    result = run(engine, tape, policy, delay=61000)
    assert len(result.entries) == 1 and result.entries[0].tick_index == 1


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_missing_quote_after_request_is_blocked_not_a_verified_nonfill(engine):
    tape = path([100.])
    policy = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None)
    result = run(engine, tape, policy)
    assert "entry_fill_quote_missing" in result.blockers
    assert result.unfilled is False


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_observed_fills_cannot_be_shifted_by_hypothetical_execution_delay(engine):
    tape = replace(path([100., 100.]), entry_evidence_kind="actual_mt5")
    policy = genome(entry_mode="actual_mt5", entry_value=None, entry_confirmation_value=None)
    assert "entry_fill_latency_requires_hypothetical_entries" in run(engine, tape, policy).blockers


@pytest.mark.parametrize("value", [-1, True, 1.5])
@pytest.mark.parametrize("contract", [ExecutionAssumptions, ExecutionScenario])
def test_fill_latency_contract_rejects_invalid_values(contract, value):
    with pytest.raises(ValueError, match="entry_fill_latency_ms"):
        contract(entry_fill_latency_ms=value)


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_exit_while_entry_request_is_in_flight_is_explicitly_blocked(engine):
    tape = path([100., 100., 98.5, 100., 98.5], offsets=[0, 2, 3, 4, 5])
    tape = replace(tape, provider_events=(ProviderEvent(BASE + timedelta(seconds=4), "CLOSE_ALL", {}),))
    policy = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None,
                    provider_management_mode="exact")
    result = run(engine, tape, policy, delay=2000)
    assert "entry_request_in_flight_at_strategy_exit" in result.blockers


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_provider_close_cancels_an_entry_that_has_not_been_requested(engine):
    tape = path([100., 98., 99.5, 100.])
    tape = replace(tape, provider_events=(ProviderEvent(BASE + timedelta(milliseconds=1500), "CLOSE_ALL", {}),))
    result = run(engine, tape, genome(provider_management_mode="explicit_close_only"), delay=0)
    assert result.entries == () and result.unfilled is True
    assert result.blockers == ()


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_provider_close_while_flat_cancels_later_unrequested_ladder_legs(engine):
    tape = path([100., 101., 100., 98.5, 100.])
    tape = replace(tape, provider_events=(ProviderEvent(BASE + timedelta(seconds=2), "CLOSE_ALL", {}),))
    policy = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None,
                    target_mode="per_leg_steps", target_steps=(0.5, 0.5),
                    provider_management_mode="explicit_close_only")
    result = run(engine, tape, policy, delay=0)
    assert len(result.entries) == 1
    assert result.exits[0].tick_index == 1 and result.exits[0].reason == "per_leg_target"


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_provider_close_racing_first_fill_is_blocked_not_assumed_instant(engine):
    tape = path([100., 100., 100.])
    tape = replace(tape, provider_events=(ProviderEvent(BASE + timedelta(milliseconds=500), "CLOSE_ALL", {}),))
    policy = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None,
                    provider_management_mode="explicit_close_only")
    result = run(engine, tape, policy, delay=1000)
    assert "provider_close_during_first_entry_execution_unmodeled" in result.blockers
    assert result.unfilled is False


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_ignored_provider_close_does_not_cancel_an_entry(engine):
    tape = path([100., 98., 99.5, 100.])
    tape = replace(tape, provider_events=(ProviderEvent(BASE + timedelta(milliseconds=1500), "CLOSE_ALL", {}),))
    assert len(run(engine, tape, genome(), delay=0).entries) == 1


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
@pytest.mark.parametrize("pending_policy", ["none", "until_expiry"])
def test_future_missing_fill_never_erases_a_closed_prefix(engine, pending_policy):
    policy = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None,
                    target_mode="per_leg_steps", target_steps=(0.5, 0.5),
                    pending_entry_policy=pending_policy)
    prefix = run(engine, path([100., 100., 101.]), policy)
    extended = run(engine, path([100., 100., 101., 98.]), policy)
    assert len(prefix.entries) == len(prefix.exits) == 1
    assert extended.entries == prefix.entries
    assert extended.exits == prefix.exits
    assert extended.pnl_eur == prefix.pnl_eur == Decimal("2.00")
    if pending_policy == "until_expiry":
        assert "entry_fill_quote_missing" in extended.blockers
        assert extended.last_tick_index == 3
    else:
        assert extended.blockers == prefix.blockers == ()
        assert extended.last_tick_index == prefix.last_tick_index == 2


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_missing_later_fill_keeps_existing_position_history(engine):
    policy = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None)
    result = run(engine, path([100., 100., 98.]), policy)
    assert len(result.entries) == len(result.exits) == 1
    assert result.entries[0].tick_index == 1
    assert result.exits[0].tick_index == 2
    assert "entry_fill_quote_missing" in result.blockers
    assert "path_ended_before_strategy_exit" in result.blockers


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_entry_records_preserve_separate_request_times(engine):
    policy = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None)
    result = run(engine, path([100., 100., 98.5, 98.2, 98.2]), policy)
    assert [row.requested_ns for row in result.entries] == [BASE_NS, BASE_NS + 2_000_000_000]
    assert [row.tick_index for row in result.entries] == [1, 3]


def test_oracle_rejects_request_time_mismatch_even_when_fills_match():
    tape = path([100., 100., 101.])
    policy = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None,
                    target_mode="per_leg_steps", target_steps=(0.5, 0.5))
    result = run("scalar", tape, policy)
    changed = replace(result, entries=(replace(result.entries[0], requested_ns=BASE_NS + 1),))
    certificate = certify_candidate((tape,), policy, (changed,),
                                    execution=ExecutionScenario(entry_fill_latency_ms=1000))
    assert certificate.status == "blocked"
    assert [row.field for row in certificate.mismatches] == ["entries[0].requested_ns"]
