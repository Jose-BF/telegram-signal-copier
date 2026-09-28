"""Missing conversion must not become a false decision or complete risk."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import numpy as np
import pytest

from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.evolution import CandidateEvaluation
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import oracle_simulate
from research.dubai_iterative.portfolio import reconstruct_portfolio
from research.execution_profile import execution_to_scenario
from tests.test_iterative_entry_fill_latency import BASE, path
from tests.test_iterative_market import market
from tests.test_iterative_protection import policy, profile


@pytest.fixture(params=["scalar", "fast", "oracle"])
def evaluate(request):
    def run(tape, strategy, *, lifecycle="market"):
        execution = ExecutionAssumptions()
        if lifecycle in {"protection", "market"}:
            execution = replace(execution, protection=profile(
                processing_delay_ms=0, acknowledgement_delay_ms=0))
        if lifecycle == "market":
            execution = replace(execution, market=market(
                entry_acknowledgement_delay_ms=0, close_processing_delay_ms=0,
                close_acknowledgement_delay_ms=0))
        if request.param == "scalar":
            return simulate(tape, strategy, execution=execution)
        if request.param == "fast":
            return FastEvaluator(execution=execution)(tape, strategy)
        return oracle_simulate(tape, strategy, execution=execution_to_scenario(execution))
    return run


def money_tape(quotes, *, offsets=None, missing=(2,), direction="BUY"):
    tape = path(quotes, offsets=offsets, direction=direction)
    mask = np.ones(len(quotes), dtype=bool)
    mask[list(missing)] = False
    return replace(tape, fx_valid=mask,
                   conversion_orientation="account_base_profit_quote")


def close_policy(**changes):
    return policy(target_mode="none", target_steps=(), trailing_distance=None,
                  provider_management_mode="explicit_close_only", **changes)


def provider_close(tape, seconds=4):
    return replace(tape, provider_events=(
        ProviderEvent(BASE + timedelta(seconds=seconds), "CLOSE_ALL", {}),))


@pytest.mark.parametrize("lifecycle", ["legacy", "market"])
def test_missing_unarmed_profit_lock_peak_blocks_result(evaluate, lifecycle):
    tape = provider_close(money_tape([100, 100, 110, 100, 100, 100]))
    strategy = close_policy(profit_lock_arm=30., profit_lock_giveback=1.)
    complete = evaluate(replace(tape, fx_valid=np.ones(6, dtype=bool)), strategy, lifecycle=lifecycle)
    assert complete.blockers == ()
    assert complete.exits[0].reason == "profit_lock"
    result = evaluate(tape, strategy, lifecycle=lifecycle)
    assert "stale_conversion_during_profit_lock" in result.blockers
    assert result.pnl_eur is None
    assert result.max_favourable_eur is None
    assert result.max_adverse_eur is None
    assert result.max_floating_drawdown_eur is None


@pytest.mark.parametrize("lifecycle", ["legacy", "market"])
@pytest.mark.parametrize("mode,extreme", [
    ("non_negative", 110), ("profit_only", 110), ("loss_only", 90),
])
def test_unknown_money_at_conditional_time_exit_is_not_false(evaluate, lifecycle, mode, extreme):
    tape = provider_close(money_tape([100, 100, extreme, 100, 100, 100],
                                   offsets=[0, 1, 61, 62, 63, 64]), seconds=63)
    result = evaluate(tape, close_policy(time_exit_min=1, time_exit_mode=mode), lifecycle=lifecycle)
    assert "stale_conversion_at_time_exit" in result.blockers
    assert result.pnl_eur is None


@pytest.mark.parametrize("lifecycle", ["legacy", "protection", "market"])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_price_policy_keeps_known_exit_money_but_not_incomplete_risk(evaluate, lifecycle, direction):
    quotes = [100, 100, 90, 100, 101, 101]
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    tape = money_tape(quotes, direction=direction)
    strategy = policy(trailing_distance=None)
    complete = evaluate(replace(tape, fx_valid=np.ones(6, dtype=bool)), strategy, lifecycle=lifecycle)
    result = evaluate(tape, strategy, lifecycle=lifecycle)
    assert complete.blockers == ()
    assert result.exits == complete.exits
    assert result.pnl_eur == complete.pnl_eur == Decimal("2.00")
    assert result.blockers == ("incomplete_equity_conversion",)
    assert result.max_favourable_eur is None
    assert result.max_adverse_eur is None
    assert result.max_floating_drawdown_eur is None


@pytest.mark.parametrize("lifecycle", ["legacy", "protection", "market"])
def test_fx_gap_after_exit_does_not_invalidate_known_exposure(evaluate, lifecycle):
    tape = money_tape([100, 100, 101, 101, 101], missing=(4,))
    result = evaluate(tape, policy(trailing_distance=None), lifecycle=lifecycle)
    assert result.blockers == ()
    assert result.pnl_eur == Decimal("2.00")
    assert result.max_adverse_eur is not None


@pytest.mark.parametrize("lifecycle", ["legacy", "protection", "market"])
def test_identity_currency_does_not_require_fx(evaluate, lifecycle):
    tape = replace(money_tape([100, 100, 90, 100, 101, 101], missing=(0, 1, 2, 3, 4, 5)),
                   conversion_orientation="identity")
    result = evaluate(tape, policy(trailing_distance=None), lifecycle=lifecycle)
    assert result.blockers == ()
    assert result.pnl_eur == Decimal("2.00")
    assert result.max_adverse_eur < Decimal("-40")


@pytest.mark.parametrize("lifecycle", ["legacy", "market"])
@pytest.mark.parametrize("mode", ["none", "non_negative"])
def test_unneeded_time_money_does_not_poison_known_exits(evaluate, mode, lifecycle):
    offsets = [0, 1, 61, 62, 63, 64] if mode == "none" else None
    tape = provider_close(money_tape([100, 100, 110, 100, 100, 100], offsets=offsets),
                          seconds=63 if mode == "none" else 4)
    result = evaluate(tape, close_policy(time_exit_min=1, time_exit_mode=mode), lifecycle=lifecycle)
    assert result.blockers == ("incomplete_equity_conversion",)
    assert result.pnl_eur == Decimal("-0.80")


@pytest.mark.parametrize("changes,blocker", [
    ({"stop_mode": "basket_money", "stop_value": 1000.}, "stale_conversion_at_basket_stop"),
    ({"target_mode": "fixed_basket", "target_value": 1000.}, "stale_conversion_at_basket_target"),
    ({"target_mode": "partial_runner", "target_value": 1000., "runner_target": 2000.,
      "partial_fraction": .5}, "stale_conversion_at_basket_target"),
    ({"hard_stop_eur_per_leg": 1000.}, "stale_conversion_at_hard_stop"),
])
def test_unknown_conversion_cannot_decide_that_money_threshold_is_not_hit(evaluate, changes, blocker):
    tape = provider_close(money_tape([100, 100, 110, 100, 100, 100]))
    strategy = close_policy().with_change(**changes)
    result = evaluate(tape, strategy, lifecycle="legacy")
    assert any(row.startswith(blocker) for row in result.blockers)
    assert result.pnl_eur is None


@pytest.mark.parametrize("lifecycle", ["legacy", "protection", "market"])
def test_unknown_per_leg_stop_does_not_use_placeholder_rate(evaluate, lifecycle):
    tape = money_tape([100, 100, 90, 100, 101, 101])
    result = evaluate(tape, policy(trailing_distance=None, hard_stop_eur_per_leg=1000.),
                      lifecycle=lifecycle)
    assert any(row.startswith("stale_conversion_at_hard_stop") for row in result.blockers)
    assert result.pnl_eur is None


def test_candidate_and_portfolio_preserve_risk_blocker_and_denominator(evaluate):
    tape = money_tape([100, 100, 90, 100, 101, 101])
    strategy = policy(trailing_distance=None)
    result = evaluate(tape, strategy, lifecycle="legacy")
    assessment = CandidateEvaluation.from_results(strategy, [(tape.day, result)])
    assert assessment.total_signal_count == assessment.filled_signal_count == 1
    assert assessment.net_eur == Decimal("2.00")
    assert assessment.blockers == ("incomplete_equity_conversion",)
    assert assessment.max_drawdown_eur is None
    assert assessment.normalized_max_drawdown_per_001 is None
    portfolio = reconstruct_portfolio([tape], [result])
    assert "simulation_blocked:control:incomplete_equity_conversion" in portfolio.blockers
    assert portfolio.evidence_complete is False
    assert portfolio.net_eur is portfolio.max_drawdown_eur is None
