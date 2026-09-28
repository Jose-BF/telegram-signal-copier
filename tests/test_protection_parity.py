"""Seeded synthetic state-machine checks, not strategy selection or MT5 proof."""

import numpy as np
import pytest

from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import ExecutionScenario, certify_candidate
from research.dubai_iterative.protection_contract import ProtectionProfile
from tests.test_iterative_entry_fill_latency import genome, path


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("seed", range(16))
@pytest.mark.parametrize("engine", ["scalar", "fast"])
def test_seeded_protection_state_transitions_match_independent_oracle(seed, direction, engine):
    rng = np.random.default_rng(seed)
    quotes = (10000 + np.r_[0, np.cumsum(rng.choice([-250, -100, -30, 0, 30, 100, 250], 39))]) / 100
    if direction == "SELL":
        quotes = 200 - quotes
    tape = path(quotes, direction=direction, offsets=np.arange(len(quotes)) / 5)
    policy = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None,
                    trailing_distance=2., target_mode="per_leg_steps", target_steps=(.5, 1.))
    profile = ProtectionProfile(.01, 2, 20, 0, [0, 200, 1000][seed % 3],
                                [0, 250][seed % 2], [0, 500][(seed // 2) % 2])
    execution = ExecutionAssumptions(entry_fill_latency_ms=[0, 200][seed % 2], protection=profile)
    result = (simulate(tape, policy, execution=execution) if engine == "scalar"
              else FastEvaluator(execution=execution)(tape, policy))
    certificate = certify_candidate((tape,), policy, (result,), execution=ExecutionScenario(
        entry_fill_latency_ms=execution.entry_fill_latency_ms, protection=profile))
    assert certificate.mismatches == ()
    assert certificate.promotion_eligible is False


@pytest.mark.parametrize("engine", ["scalar", "fast"])
def test_partial_passive_exit_does_not_label_open_remainder_as_closed(engine):
    tape = path([100., 100., 101., 98.5, 98.5])
    policy = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None,
                    trailing_distance=30., target_mode="per_leg_steps", target_steps=(.5, 1.))
    profile = ProtectionProfile(.01, 2, 20, 0, 1000, 1000, 1000)
    execution = ExecutionAssumptions(protection=profile)
    result = (simulate(tape, policy, execution=execution) if engine == "scalar"
              else FastEvaluator(execution=execution)(tape, policy))
    certificate = certify_candidate((tape,), policy, (result,), execution=ExecutionScenario(protection=profile))
    assert len(result.entries) == 2 and len(result.exits) == 1
    assert result.exit_reason == "not_closed" and result.pnl_eur is None
    assert certificate.mismatches == ()
