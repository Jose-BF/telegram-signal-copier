"""Bounded synthetic lifecycle interaction checks, never strategy selection."""

from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest

from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.oracle import ExecutionScenario, certify_candidate
from tests.test_iterative_protection import BASE, path, policy, profile


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("mode", ["simultaneous", "adverse", "favourable"])
@pytest.mark.parametrize("budget", [1, 2, 4, 7, 11, 20, 100])
def test_three_engines_preserve_same_bounded_multi_leg_prefix(direction, mode, budget):
    quotes = np.asarray([100., 100., 97., 103., 103., 96., 96., 69., 69., 69., 69.])
    if direction == "SELL":
        quotes = 200 - quotes
    tape = replace(path(quotes, direction=direction), provider_events=(
        ProviderEvent(BASE + timedelta(seconds=5), "CLOSE_ALL", {}),))
    strategy = policy(leg_count=3, volume_weights=(.04, .01, .04),
                      target_steps=(.5, 1., 1.5), entry_ladder_mode=mode,
                      entry_ladder_step=None if mode == "simultaneous" else 1.5,
                      provider_management_mode="explicit_close_only")
    market = MarketProfile(1000, 1000, 1000, .02, 1., .01, max_events=budget)
    execution = ExecutionAssumptions(entry_fill_latency_ms=1000, market=market, protection=profile())
    scenario = ExecutionScenario(entry_fill_latency_ms=1000, market=market, protection=execution.protection)
    scalar = simulate(tape, strategy, execution=execution)
    fast = FastEvaluator(execution=execution)(tape, strategy)
    for name, result in (("scalar", scalar), ("fast", fast)):
        certificate = certify_candidate((tape,), strategy, (result,), execution=scenario)
        assert certificate.mismatches == (), (name, certificate.mismatches)
        assert certificate.promotion_eligible is False
    assert fast == scalar
