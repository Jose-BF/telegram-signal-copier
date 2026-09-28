from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest

from research.dubai_current_comparison import current_genome
from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.oracle import certify_candidate
from research.execution_profile import execution_to_scenario
from tests.test_iterative_protection import BASE, path, profile


def execution(**changes):
    return replace(ExecutionAssumptions(entry_fill_latency_ms=1000,
        protection=profile(policy_extension="basket_guard_v1", request_quote_binding="timestamp_and_ordinal"),
        market=MarketProfile(0, 1000, 0, .01, 1., .01)), **changes)


def policy(**changes):
    base, _ = current_genome()
    if changes.get("leg_count") == 1:
        changes.update(entry_ladder_mode="simultaneous", entry_ladder_step=None)
    return replace(base, **changes)


def parity(tape, strategy, assumptions=None):
    assumptions = assumptions or execution()
    scalar = simulate(tape, strategy, execution=assumptions)
    fast = FastEvaluator(execution=assumptions)(tape, strategy)
    assert scalar == fast
    proof = certify_candidate((tape,), strategy, (scalar,), execution=execution_to_scenario(assumptions))
    assert not proof.mismatches, proof.mismatches
    assert proof.promotion_eligible is False
    return scalar


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_basket_stop_is_a_delayed_market_close_not_a_native_price_stop(direction):
    quotes = np.array([100.,100.,98.,97.,97.])
    tape = path(quotes if direction == "BUY" else 200-quotes, direction=direction)
    result = parity(tape, policy(leg_count=1, volume_weights=(.04,), stop_value=5.))
    assert not result.blockers
    assert [e.tick_index for e in result.market_events if e.kind == "close_requested"] == [2]
    assert result.exits[0].tick_index == 3 and result.exits[0].reason == "basket_stop"
    assert result.pnl_eur < -5
    assert all(e.sl is None for e in result.protection_events)


def test_basket_stop_wins_over_provider_close_on_same_quote():
    tape = replace(path([100.,100.,98.,97.,97.]),
        provider_events=(ProviderEvent(BASE + timedelta(seconds=2), "CLOSE_ALL", {}),))
    result = parity(tape, policy(leg_count=1, volume_weights=(.04,), stop_value=5.))
    assert result.exit_reason == "basket_stop"


def test_provider_close_wins_over_profit_giveback():
    tape = replace(path([100.,100.,105.,103.,103.,103.]),
        provider_events=(ProviderEvent(BASE + timedelta(seconds=3), "CLOSE_PARTIAL", {}),))
    result = parity(tape, policy(leg_count=1, volume_weights=(.04,)))
    assert not result.blockers and result.exit_reason == "provider_close"


def test_positive_basket_does_not_close_at_time_limit_then_closes_when_negative():
    tape = path([100.,100.,101.,99.,99.,99.], offsets=[0,1,2402,2403,2404,2405])
    result = parity(tape, policy(leg_count=1, volume_weights=(.01,)))
    assert not result.blockers
    assert result.exits[0].tick_index == 4 and result.exit_reason == "time_exit"


def test_positive_open_basket_is_censored_not_forced_closed_at_data_end():
    tape = path([100.,100.,101.,101.], offsets=[0,1,2402,3900])
    result = parity(tape, policy(leg_count=1, volume_weights=(.01,)))
    assert "path_ended_before_strategy_exit" in result.blockers
    assert not result.exits and result.pnl_eur is None


def test_stale_conversion_blocks_basket_money_in_all_three_engines():
    tape = replace(path([100.,100.,98.,97.,97.]), conversion_orientation="account_base_profit_quote",
                   fx_valid=np.array([True, True, False, False, False]))
    result = parity(tape, policy(leg_count=1, volume_weights=(.04,), stop_value=5.))
    assert "stale_conversion_at_basket_stop" in result.blockers
    assert result.pnl_eur is None


def test_basket_close_cancels_future_ladder_requests():
    result = parity(path([100.,100.,94.,93.,80.,80.,80.]), policy(stop_value=5., entry_ladder_step=12.))
    assert not result.blockers and len(result.entries) == 1
    assert result.exit_reason == "basket_stop"


def test_old_profile_keeps_basket_gate_closed():
    assumptions = execution(protection=profile())
    result = parity(path([100.,100.,98.,97.,97.]), policy(), assumptions)
    assert "protection_policy_unsupported" in result.blockers


def test_new_profile_requires_market_execution():
    result = parity(path([100.,100.]), policy(), execution(market=None))
    assert "basket_guard_requires_hypothetical_market" in result.blockers


def test_new_profile_does_not_admit_other_protection_families():
    result = parity(path([100.,100.]), policy(stop_mode="fixed_move", stop_value=5.))
    assert "basket_guard_policy_contract" in result.blockers


@pytest.mark.parametrize("seed", range(12))
def test_three_engine_randomized_multi_leg_basket_lifecycle(seed):
    rng = np.random.default_rng(seed + 123)
    quotes = np.round(100 + np.cumsum(rng.normal(0, 2.0, 150)), 2)
    direction = "BUY" if seed % 2 else "SELL"
    tape = path(quotes if direction == "BUY" else 200-quotes, direction=direction)
    tape = replace(tape, provider_events=(ProviderEvent(BASE+timedelta(seconds=110), "CLOSE_ALL", {}),))
    parity(tape, policy(time_exit_min=1), execution(entry_fill_latency_ms=seed % 3 * 1000))
