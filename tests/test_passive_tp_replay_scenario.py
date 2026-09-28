"""Installed TP stress worlds must preserve exposure until a hypothetical fill."""

import pytest
from random import Random

from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.passive_fill_contract import PassiveFillScenario
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.protection import ProtectionBook
from research.dubai_iterative.oracle import _OracleProtectionLifecycle
from tests.test_oracle_protection import policy, profile, run_oracle, run_scalar
from tests.test_iterative_entry_fill_latency import path
from tests.test_oracle_market import market_profile, policy as market_policy
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.oracle import (
    ExecutionScenario, certify_candidate, oracle_simulate,
)


def test_delayed_installed_tp_keeps_position_open_across_adverse_quotes():
    tape = path([100.0, 100.0, 101.0, 99.0, 99.0, 99.0])
    delayed = profile(passive_fill_scenario=PassiveFillScenario(
        2_000_000_000, "installed_level"))

    scalar = run_scalar(tape, protection=delayed)
    oracle = run_oracle(tape, protection=delayed)
    fast = FastEvaluator(execution=ExecutionAssumptions(protection=delayed))(
        tape, policy())
    immediate = run_scalar(tape)

    assert [(event.kind, event.tick_index) for event in scalar.protection_events
            if event.kind in {"touched", "closed"}] == [("touched", 2), ("closed", 4)]
    assert scalar.exits[0].tick_index == 4
    assert scalar.exits[0].exit_price == immediate.exits[0].exit_price
    assert scalar.max_floating_drawdown_eur > immediate.max_floating_drawdown_eur
    assert oracle.protection_events == scalar.protection_events
    assert oracle.exits[0].tick_index == scalar.exits[0].tick_index
    assert oracle.exits[0].exit_price == scalar.exits[0].exit_price
    assert fast.protection_events == scalar.protection_events
    assert fast.exits == scalar.exits
    assert fast.max_floating_drawdown_eur == scalar.max_floating_drawdown_eur


def test_executable_quote_world_uses_quote_after_due_and_no_duplicate_touch():
    tape = path([100.0, 100.0, 101.0, 100.0, 99.0, 99.0])
    scenario = profile(passive_fill_scenario=PassiveFillScenario(
        2_000_000_000, "executable_quote"))

    scalar = run_scalar(tape, protection=scenario)
    oracle = run_oracle(tape, protection=scenario)
    fast = FastEvaluator(execution=ExecutionAssumptions(protection=scenario))(
        tape, policy())

    assert [event.kind for event in scalar.protection_events].count("touched") == 1
    assert scalar.exits[0].tick_index == 4
    assert scalar.exits[0].exit_price == 98.8
    assert oracle.exits[0].exit_price == scalar.exits[0].exit_price
    assert fast.exits == scalar.exits


@pytest.mark.parametrize(("price_mode", "expected_price"), [
    ("installed_level", 100.5),
    ("executable_quote", 100.8),
])
def test_zero_delay_scenario_touches_and_closes_on_same_quote(
        price_mode, expected_price):
    tape = path([100.0, 100.0, 101.0])
    scenario = profile(passive_fill_scenario=PassiveFillScenario(0, price_mode))
    scalar = run_scalar(tape, protection=scenario)
    oracle = run_oracle(tape, protection=scenario)
    fast = FastEvaluator(execution=ExecutionAssumptions(protection=scenario))(
        tape, policy())

    assert [(event.kind, event.tick_index) for event in scalar.protection_events
            if event.kind in {"touched", "closed"}] == [("touched", 2), ("closed", 2)]
    assert scalar.exits[0].exit_price == expected_price
    assert scalar.exits == fast.exits
    assert scalar.protection_events == oracle.protection_events == fast.protection_events


def test_quote_missing_after_due_is_not_reported_as_a_completed_exit():
    tape = path([100.0, 100.0, 101.0, 99.0])
    scenario = profile(passive_fill_scenario=PassiveFillScenario(
        5_000_000_000, "installed_level"))

    for result in (run_scalar(tape, protection=scenario),
                   run_oracle(tape, protection=scenario),
                   FastEvaluator(execution=ExecutionAssumptions(
                       protection=scenario))(tape, policy())):
        assert result.exits == ()
        assert "path_ended_before_strategy_exit" in result.blockers
        assert [event.kind for event in result.protection_events].count("touched") == 1


def test_sell_tp_waits_for_due_quote_after_price_retreats():
    tape = path([100.0, 100.0, 99.0, 101.0, 101.0], direction="SELL")
    scenario = profile(passive_fill_scenario=PassiveFillScenario(
        2_000_000_000, "executable_quote"))

    scalar = run_scalar(tape, protection=scenario)
    oracle = run_oracle(tape, protection=scenario)
    fast = FastEvaluator(execution=ExecutionAssumptions(protection=scenario))(
        tape, policy())

    assert scalar.exits[0].tick_index == oracle.exits[0].tick_index == 4
    assert scalar.exits[0].exit_price == oracle.exits[0].exit_price == 101.2
    assert scalar.protection_events == oracle.protection_events
    assert fast.exits == scalar.exits


@pytest.mark.parametrize("book_type", [ProtectionBook, _OracleProtectionLifecycle])
def test_pending_tp_rejects_in_flight_protection_change(book_type):
    scenario = profile(passive_fill_scenario=PassiveFillScenario(
        3_000_000_000, "installed_level"))
    book = book_type(scenario, "BUY")
    book.open("p", 0, 0, 90.0, 101.0, "test")
    book.request("p", 0, 0, 95.0, 104.0, "be", "tp")
    if book_type is ProtectionBook:
        assert book.hit("p", 102.0, index=1, now=1_000_000_000) is None
    else:
        assert book.passive_hit("p", 102.0, 1, 1_000_000_000) is None
    book.process(1, 1_000_000_000, 102.0)

    assert [(event.kind, event.reason) for event in book.events][-2:] == [
        ("touched", "initial_tp"), ("rejected", "passive_fill_pending")]
    assert book.states["p"].sl == 90.0
    assert book.states["p"].tp == 101.0


def test_three_engines_reject_modification_while_tp_fill_is_pending():
    tape = path([100.0, 100.0, 101.0, 99.0, 99.0, 99.0, 99.0])
    scenario = profile(passive_fill_scenario=PassiveFillScenario(
        3_000_000_000, "installed_level"))
    scalar = run_scalar(tape, protection=scenario)
    oracle = run_oracle(tape, protection=scenario)
    fast = FastEvaluator(execution=ExecutionAssumptions(protection=scenario))(
        tape, policy())

    assert [(event.kind, event.reason) for event in scalar.protection_events
            if event.kind == "rejected"] == [("rejected", "passive_fill_pending")]
    assert scalar.protection_events == oracle.protection_events == fast.protection_events
    assert scalar.exits == fast.exits
    assert oracle.exits[0].tick_index == scalar.exits[0].tick_index == 5


def test_two_legs_keep_independent_pending_tp_clocks():
    tape = path([100.0, 100.0, 101.0, 102.0, 99.0, 99.0, 99.0])
    strategy = policy(leg_count=2, volume_weights=(0.04, 0.03),
                      target_steps=(0.5, 1.5))
    scenario = profile(passive_fill_scenario=PassiveFillScenario(
        2_000_000_000, "installed_level"))

    scalar = run_scalar(tape, protection=scenario, strategy=strategy)
    oracle = run_oracle(tape, protection=scenario, strategy=strategy)
    fast = FastEvaluator(execution=ExecutionAssumptions(protection=scenario))(
        tape, strategy)

    assert [(event.ticket, event.tick_index) for event in scalar.protection_events
            if event.kind == "touched"] == [("sim_1", 2), ("sim_2", 3)]
    assert [(event.ticket, event.tick_index) for event in scalar.exits] == [
        ("sim_1", 4), ("sim_2", 5)]
    assert scalar.protection_events == oracle.protection_events == fast.protection_events
    assert scalar.exits == fast.exits
    assert [(row.ticket, row.tick_index) for row in oracle.exits] == [
        (row.ticket, row.tick_index) for row in scalar.exits]


def test_fast_engine_replays_same_delayed_tp_lifecycle():
    tape = path([100.0, 100.0, 101.0, 99.0, 99.0])
    scenario = profile(passive_fill_scenario=PassiveFillScenario(
        2_000_000_000, "installed_level"))

    result = FastEvaluator(execution=ExecutionAssumptions(protection=scenario))(
        tape, policy())

    assert result.exits[0].tick_index == 4
    assert [(event.kind, event.tick_index) for event in result.protection_events
            if event.kind in {"touched", "closed"}] == [("touched", 2), ("closed", 4)]
    assert result.blockers == ()


@pytest.mark.parametrize(("delay_ns", "expected_reason"), [
    (2_000_000_000, "per_leg_target"),
    (3_000_000_000, "hard_stop_per_leg"),
])
def test_competing_market_close_and_pending_tp_have_one_winner(
        delay_ns, expected_reason):
    tape = path([100.0, 100.0, 101.0, 99.0, 99.0, 99.0, 99.0])
    strategy = market_policy(target_mode="per_leg_steps", target_steps=(0.5,),
                             hard_stop_eur_per_leg=1.0)
    protection = profile(passive_fill_scenario=PassiveFillScenario(
        delay_ns, "installed_level"))
    market = market_profile(entry_acknowledgement_delay_ms=0)

    scalar = simulate(tape, strategy, execution=ExecutionAssumptions(
        protection=protection, market=market))
    oracle = oracle_simulate(tape, strategy, execution=ExecutionScenario(
        protection=protection, market=market))
    fast = FastEvaluator(execution=ExecutionAssumptions(
        protection=protection, market=market))(tape, strategy)

    assert len(scalar.exits) == len(oracle.exits) == 1
    assert scalar.exits[0].reason == oracle.exits[0].reason == expected_reason
    assert scalar.exits[0].tick_index == oracle.exits[0].tick_index == 4
    assert [event.kind for event in scalar.protection_events].count("touched") == 1
    assert scalar.protection_events == oracle.protection_events
    assert scalar.market_events == oracle.market_events
    assert fast.exits == scalar.exits
    assert fast.protection_events == scalar.protection_events
    assert fast.market_events == scalar.market_events


@pytest.mark.parametrize("with_market", [False, True])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("seed", range(8))
def test_bounded_multi_leg_paths_agree_across_three_motors(
        seed, direction, with_market):
    rng = Random(seed)
    value = 10_000
    quotes = [100.0]
    for _ in range(19):
        value += rng.choice((-200, -150, -50, 0, 50, 100, 200))
        quotes.append(value / 100)
    if direction == "SELL":
        quotes = [200 - quote for quote in quotes]
    tape = path(quotes, direction=direction)
    strategy = (market_policy if with_market else policy)(
        leg_count=2, volume_weights=(0.04, 0.03),
        entry_ladder_mode="adverse", entry_ladder_step=1.5,
        target_mode="per_leg_steps", target_steps=(0.5, 1.0),
        trailing_distance=5.0,
    )
    protection = profile(passive_fill_scenario=PassiveFillScenario(
        (0, 1_000_000_000, 3_000_000_000)[seed % 3],
        ("installed_level", "executable_quote")[seed % 2]))
    market = market_profile(entry_acknowledgement_delay_ms=(0, 1000)[seed % 2],
                            close_processing_delay_ms=(0, 1000)[seed % 2]) if with_market else None
    scalar_execution = ExecutionAssumptions(
        protection=protection, market=market, exit_slippage=0.05)
    oracle_execution = ExecutionScenario(
        protection=protection, market=market, exit_slippage=0.05)

    scalar = simulate(tape, strategy, execution=scalar_execution)
    fast = FastEvaluator(execution=scalar_execution)(tape, strategy)
    certificate = certify_candidate((tape,), strategy, (scalar,),
                                    execution=oracle_execution)

    assert fast == scalar, (seed, direction, with_market)
    assert certificate.mismatches == (), (seed, direction, with_market,
                                          certificate.mismatches)
