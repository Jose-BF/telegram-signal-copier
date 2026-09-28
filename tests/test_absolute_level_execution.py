"""Absolute published prices use the existing three execution lifecycles."""

from dataclasses import asdict, replace
import json

import numpy as np
import pytest

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.oracle import ExecutionScenario, oracle_simulate
from tests.test_iterative_protection import path, profile


def policy(direction="BUY", **changes):
    base = StrategyGenome(schema_version=2, entry_mode="published_range", entry_value=96 if direction == "BUY" else 100,
        entry_confirmation_value=100 if direction == "BUY" else 104, entry_expiry_min=5,
        leg_count=1, volume_weights=(.04,), stop_mode="fixed_level", stop_value=90 if direction == "BUY" else 110,
        target_mode="per_leg_levels", target_steps=(105 if direction == "BUY" else 95,), be_mode="none",
        provider_management_mode="ignore", time_exit_mode="always", time_exit_min=1)
    return replace(base, **changes)


def execution(**changes):
    return replace(ExecutionAssumptions(entry_fill_latency_ms=1000,
        market=MarketProfile(0, 0, 0, .01, 1., .01),
        protection=profile(policy_extension="absolute_levels_v1", request_quote_binding="timestamp_and_ordinal")), **changes)


def parity(tape, genome, assumptions=None):
    assumptions = assumptions or execution()
    scenario = ExecutionScenario(**{k:v for k,v in asdict(assumptions).items() if k not in {"protection", "market", "client"}},
        protection=assumptions.protection, market=assumptions.market, client=assumptions.client)
    results = [simulate(tape, genome, execution=assumptions), FastEvaluator(execution=assumptions)(tape, genome),
               oracle_simulate(tape, genome, execution=scenario)]
    dictionaries = [{k:v for k,v in json.loads(json.dumps(asdict(r), default=str)).items() if k != "behavior_digest"} for r in results]
    assert dictionaries[0] == dictionaries[1] == dictionaries[2]
    return results[0]


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_absolute_sl_tp_do_not_move_with_delayed_fill(direction):
    q = np.array([100., 99., 101., 106., 106.])
    result = parity(path(q if direction == "BUY" else 200-q, direction=direction), policy(direction))
    assert not result.blockers
    assert result.entries[0].entry_price == (99 if direction == "BUY" else 101)
    assert result.exits[0].exit_price == (105 if direction == "BUY" else 95)
    assert all(event.sl in {90, 110} and event.tp in {95, 105} for event in result.protection_events)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_range_ladder_uses_published_midpoint_and_far_edge(direction):
    q = np.array([102., 100., 98.5, 98., 97., 96., 95.5, 101., 106., 106.])
    strategy = policy(direction, entry_ladder_mode="range_levels", leg_count=3,
        volume_weights=(.06,.03,.01), target_steps=(105,105,105) if direction == "BUY" else (95,95,95))
    result = parity(path(q if direction == "BUY" else 200-q, direction=direction), strategy)
    assert not result.blockers
    assert [r.tick_index for r in result.entries] == [2,4,6]
    assert len(result.exits) == 3


@pytest.mark.parametrize("mode", ["signal_market", "published_range", "published_limit"])
def test_barrier_before_entry_cancels_thesis(mode):
    q = [106.,100.,99.,106.]
    result = parity(path(q), policy(entry_mode=mode, entry_value=99 if mode == "published_limit" else 96))
    assert not result.blockers and not result.entries


@pytest.mark.parametrize("last", [106.,89.])
def test_price_moves_through_protection_while_request_is_in_flight(last):
    result = parity(path([100.,last,last,last]), policy())
    assert not result.blockers and not result.entries
    assert result.exit_reason == "entry_rejected"


def test_split_targets_are_installed_on_their_respective_legs():
    result = parity(path([100.,99.,106.,106.,108.,108.]), policy(leg_count=2, volume_weights=(.03,.01), target_steps=(105,107)))
    assert not result.blockers
    assert [r.exit_price for r in result.exits] == [105,107]


def test_time_exit_retains_market_close_delay():
    result = parity(path([100.,99.,100.,100.,100.], offsets=[0,1,60,61,62]), policy())
    assert not result.blockers
    assert result.exits[0].tick_index == 4 and result.exit_reason == "time_exit"


def test_new_modes_cannot_run_without_explicit_profile():
    result = parity(path([100.,99.,106.]), policy(), ExecutionAssumptions())
    assert "absolute_levels_require_explicit_market_profile" in result.blockers


@pytest.mark.parametrize("changes", [{"schema_version":1}, {"be_mode":"delayed", "be_trigger":1}, {"stop_mode":"fixed_move"},
    {"entry_confirmation_value":95}, {"entry_ladder_mode":"range_levels"}, {"target_steps":()}])
def test_incomplete_absolute_contracts_rejected(changes):
    assert policy(**changes).validation_errors()


@pytest.mark.parametrize("seed", range(12))
def test_randomized_absolute_lifecycle_parity(seed):
    rng = np.random.default_rng(seed)
    quotes = np.round(100 + np.cumsum(rng.normal(0, 1.4, 90)), 2)
    assumptions = execution(entry_fill_latency_ms=(seed % 3)*1000,
        market=MarketProfile((seed % 2)*1000, 1000, 1000, .01, 1., .01))
    parity(path(quotes), policy(entry_ladder_mode="range_levels", leg_count=3, volume_weights=(.06,.03,.01),
        target_steps=(105,105,107)), assumptions)
