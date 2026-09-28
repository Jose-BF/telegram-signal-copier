from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal

import numpy as np
import pytest

from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.dataset import RolloverEvent
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.oracle import oracle_simulate
from research.execution_profile import execution_from_mapping, execution_to_scenario
from tests.test_iterative_protection import path, policy, profile
from tests.test_iterative_entry_fill_latency import BASE


def execution(**changes):
    base = ExecutionAssumptions(market=MarketProfile(1000, 1000, 1000, .01, 1., .01),
        protection=profile(policy_extension="own_rule_be_partial_v1"))
    return replace(base, **changes)


def strategy(**changes):
    return policy(volume_weights=(.04,), stop_mode="fixed_move", stop_value=10.,
        target_mode="none", target_steps=(), trailing_distance=None,
        pending_entry_policy="none").with_change(**changes)


def tape(quotes, direction="BUY", **kwargs):
    values = np.asarray(quotes, dtype=float)
    if direction == "SELL":
        values = 200 - values
    result = path(values, direction=direction, **kwargs)
    return replace(result, bid=values, ask=values, exit_quotes=values)


def compare(ticks, genome, assumptions=None):
    assumptions = assumptions or execution()
    scalar = simulate(ticks, genome, execution=assumptions)
    fast = FastEvaluator(execution=assumptions)(ticks, genome)
    oracle = oracle_simulate(ticks, genome, execution=execution_to_scenario(assumptions))
    expected = {k: v for k, v in asdict(oracle).items() if k != "behavior_digest"}
    for name, result in (("scalar", scalar), ("fast", fast)):
        values = {k: v for k, v in asdict(result).items() if k != "behavior_digest"}
        assert values == expected, (name, {k: (values.get(k), expected.get(k)) for k in values.keys() | expected.keys() if values.get(k) != expected.get(k)})
    return scalar


def partial(**changes):
    return strategy(target_mode="partial_runner", target_value=3., runner_target=8.,
        partial_fraction=.5).with_change(**changes)


def test_explicit_extension_roundtrip_and_unknown_capability_rejected():
    original = ExecutionAssumptions(market=MarketProfile(1000, 1000, 1000, .01, 1., .01), protection=profile())
    payload = asdict(original)
    payload["protection"]["policy_extension"] = "own_rule_be_partial_v1"
    decoded = execution_from_mapping(payload)
    assert decoded.protection.policy_extension == "own_rule_be_partial_v1"
    assert original.protection.policy_extension == "none"
    payload["protection"]["policy_extension"] = "anything"
    with pytest.raises(ValueError):
        execution_from_mapping(payload)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_be_is_installed_after_request_and_can_execute_before_ack(direction):
    result = compare(tape([100, 104, 104, 99, 99], direction), strategy(be_mode="price", be_trigger=3.))
    assert not result.blockers
    assert [(e.kind, e.tick_index) for e in result.protection_events] == [
        ("open", 0), ("requested", 1), ("installed", 2), ("closed", 3)]
    assert result.exits[0].reason == "break_even"
    assert result.exits[0].tick_index == 3


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_be_does_not_remember_a_peak_before_entry_ack(direction):
    assumptions = execution(market=MarketProfile(3000, 1000, 1000, .01, 1., .01))
    result = compare(tape([100, 104, 100, 100, 89, 89], direction), strategy(be_mode="price", be_trigger=3.), assumptions)
    assert not result.blockers
    assert result.exits[0].reason == "initial_sl"
    assert not any(e.kind == "requested" for e in result.protection_events)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_be_rejected_then_retried_after_response_and_retry_delay(direction):
    result = compare(tape([100, 104, 100.1, 104, 104, 104, 99, 99], direction), strategy(be_mode="price", be_trigger=3.))
    assert not result.blockers
    assert [e.tick_index for e in result.protection_events if e.kind == "requested"] == [1, 4]
    assert [e.tick_index for e in result.protection_events if e.kind == "rejected"] == [2]
    assert [e.tick_index for e in result.protection_events if e.kind == "installed"] == [5]
    assert result.exits[0].reason == "break_even"


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_old_stop_wins_before_be_installation(direction):
    result = compare(tape([100, 104, 89, 89], direction), strategy(be_mode="price", be_trigger=3.))
    assert not result.blockers
    assert result.exits[0].reason == "initial_sl"
    assert not any(e.kind == "installed" for e in result.protection_events)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_partial_keeps_residual_and_runner_waits_for_partial_ack(direction):
    result = compare(tape([100, 101, 102, 103, 104, 104], direction), partial())
    assert not result.blockers
    assert [(e.tick_index, e.volume, e.reason) for e in result.exits] == [(2, .02, "partial_target"), (4, .02, "runner_target")]
    assert [(e.tick_index, e.volume) for e in result.market_events if e.kind == "close_requested"] == [(1, .02), (3, .02)]
    assert [(e.tick_index, e.volume) for e in result.market_events if e.kind == "close_acknowledged"] == [(3, .02), (5, .02)]
    assert [(e.tick_index, e.reason) for e in result.protection_events if e.kind == "closed"] == [(4, "runner_target")]
    assert float(result.pnl_eur) == 12.
    assert len(result.entries) == 1 and result.entries[0].volume == .04


@pytest.mark.parametrize("quotes,volumes,reasons", [
    ([100, 101, 89, 89], [.04], ["initial_sl"]),
    ([100, 101, 102, 89, 89], [.02, .02], ["partial_target", "initial_sl"]),
])
def test_passive_stop_can_preempt_partial_or_close_its_residual(quotes, volumes, reasons):
    result = compare(tape(quotes), partial())
    assert not result.blockers
    assert [e.volume for e in result.exits] == volumes
    assert [e.reason for e in result.exits] == reasons
    if len(volumes) == 1:
        assert any(e.kind == "close_rejected" and e.reason == "position_already_closed" for e in result.market_events)


def test_partial_residual_can_close_by_time():
    result = compare(tape([100, 101, 102, 102, 102, 102, 102], offsets=[0, 1, 2, 3, 60, 61, 62]),
        partial(runner_target=100., time_exit_min=1, time_exit_mode="always"))
    assert not result.blockers
    assert [(e.volume, e.reason) for e in result.exits] == [(.02, "partial_target"), (.02, "time_exit")]


@pytest.mark.parametrize("budget", [1, 3, 4, 5, 6, 7, 8, 9, 10])
def test_partial_event_budget_preserves_same_prefix_across_engines(budget):
    assumptions = execution(market=MarketProfile(1000, 1000, 1000, .01, 1., .01, max_events=budget))
    result = compare(tape([100, 101, 102, 103, 104, 104]), partial(), assumptions)
    if budget < 9:
        assert "market_event_budget_exhausted" in result.blockers
    else:
        assert not result.blockers


def test_unacknowledged_partial_at_data_end_is_not_a_complete_result():
    result = compare(tape([100, 101, 102]), partial())
    assert [(e.volume, e.reason) for e in result.exits] == [(.02, "partial_target")]
    assert "market_lifecycle_incomplete_at_data_end" in result.blockers
    assert result.pnl_eur is None


def test_invalid_partial_and_residual_lot_steps_block_before_entry():
    result = compare(tape([100, 101, 102, 103]), partial(volume_weights=(.03,)))
    assert result.entries == ()
    assert "market_partial_volume_unsupported" in result.blockers


def test_base_profile_remains_blocked_for_new_families():
    assumptions = execution(protection=profile())
    for genome in (partial(), strategy(be_mode="price", be_trigger=3.)):
        result = compare(tape([100, 101, 102, 103]), genome, assumptions)
        assert result.entries == ()
        assert "protection_policy_unsupported" in result.blockers


@pytest.mark.parametrize("kind", ["without_market", "provider", "ladder", "delayed_be"])
def test_unrepresented_extension_combinations_fail_closed(kind):
    assumptions, genome = execution(), partial()
    expected = "protection_extension_requires_own_rule_market"
    if kind == "without_market":
        assumptions = replace(assumptions, market=None)
    elif kind == "provider":
        genome = genome.with_change(provider_management_mode="explicit_close_only")
    elif kind == "ladder":
        genome = genome.with_change(entry_ladder_mode="adverse", entry_ladder_step=1.,
                                    leg_count=2, volume_weights=(.04, .04))
        expected = "market_partial_ladder_unsupported"
    else:
        genome = strategy(be_mode="delayed", be_trigger=1.)
        expected = "protection_policy_unsupported"
    result = compare(tape([100, 101, 102, 103]), genome, assumptions)
    assert result.entries == () and expected in result.blockers


def test_partial_targets_are_account_money_not_price_movement():
    ticks = tape([100, 101, 102, 103, 104, 105, 106, 106])
    ticks = replace(ticks, conversion_orientation="account_base_profit_quote",
                    fx_bid=np.full(8, 2.), fx_ask=np.full(8, 2.))
    result = compare(ticks, partial())
    assert not result.blockers
    assert [e.tick_index for e in result.market_events if e.kind == "close_requested"] == [2, 5]
    assert result.pnl_eur == Decimal("9.00")


def test_partial_allocates_accrued_swap_and_later_cost_to_residual():
    charges = np.array([0, -1, -3, -4, -5], dtype=np.int64)
    ticks = replace(tape([100, 101, 102, 103, 104, 104]), rollover_events=(
        RolloverEvent(BASE + timedelta(seconds=1), charges),
        RolloverEvent(BASE + timedelta(seconds=3), charges)))
    result = compare(ticks, partial())
    assert not result.blockers
    assert [e.pnl_eur for e in result.exits] == [Decimal("3.97"), Decimal("7.95")]
    assert result.pnl_eur == Decimal("11.92")


@pytest.mark.parametrize("stale_index", [1, 2, 3, 4])
def test_stale_conversion_never_becomes_verified_partial_money(stale_index):
    ticks = tape([100, 101, 102, 103, 104, 104, 104])
    valid = ticks.fx_valid.copy()
    valid[stale_index] = False
    ticks = replace(ticks, conversion_orientation="account_base_profit_quote", fx_valid=valid)
    result = compare(ticks, partial())
    assert result.blockers and (result.pnl_eur is None or result.max_favourable_eur is None)


@pytest.mark.parametrize("budget", [1, 2, 3, 4, 5, 8, 11])
def test_protection_event_budget_preserves_be_partial_prefix(budget):
    assumptions = execution(protection=profile(policy_extension="own_rule_be_partial_v1", max_events=budget))
    compare(tape([100, 104, 104, 105, 105, 99, 99, 99]), partial(be_mode="price", be_trigger=3.), assumptions)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("variant", ["be", "partial", "partial_be", "mixed", "multi_leg"])
@pytest.mark.parametrize("delay_ms", [0, 1000, 2500])
def test_bounded_interaction_grid_has_full_three_engine_parity(direction, variant, delay_ms):
    changes = dict(time_exit_min=1, time_exit_mode="always")
    genome = strategy(be_mode="price", be_trigger=2.) if variant == "be" else partial()
    if variant in {"partial_be", "mixed"}:
        changes.update(be_mode="price", be_trigger=2.)
    if variant == "mixed":
        changes.update(trailing_distance=4., profit_lock_arm=2., profit_lock_giveback=1., hard_stop_eur_per_leg=5.)
    if variant == "multi_leg":
        changes.update(leg_count=2, volume_weights=(.04, .02))
    genome = genome.with_change(**changes)
    assumptions = execution(entry_fill_latency_ms=delay_ms,
        market=MarketProfile(delay_ms, delay_ms, delay_ms, .01, 1., .01),
        protection=profile(policy_extension="own_rule_be_partial_v1", processing_delay_ms=delay_ms,
            acknowledgement_delay_ms=delay_ms), entry_slippage=.1, exit_slippage=.1, spread_addition=.1)
    for seed in range(8):
        rng = np.random.default_rng(1700 + seed)
        quotes = np.maximum(80., 100 + np.cumsum(rng.choice([-4., -1., 0., 1., 3.], size=30)))
        quotes[0] = 100.
        quotes = np.concatenate((quotes, np.full(12, quotes[-1])))
        ticks = tape(quotes, direction, offsets=[*range(30), *range(60, 72)])
        result = compare(ticks, genome, assumptions)
        opened = sum(Decimal(str(e.volume)) for e in result.entries)
        closed = sum(Decimal(str(e.volume)) for e in result.exits)
        assert 0 <= closed <= opened <= sum(Decimal(str(v)) for v in genome.volume_weights)


@pytest.mark.parametrize("anchor", ["100.01", "2047.31", "4095.67", "8191.67"])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("trigger", ["0.3", "3"])
def test_be_threshold_is_exact_across_binary_price_boundaries(anchor, direction, trigger):
    sign = 1 if direction == "BUY" else -1
    prices = np.array([float(Decimal(anchor) + sign * Decimal(v)) for v in ("0", trigger, trigger, "-1", "-1")])
    ticks = replace(path(prices, direction=direction), bid=prices, ask=prices, exit_quotes=prices)
    result = compare(ticks, strategy(be_mode="price", be_trigger=float(trigger)))
    assert not result.blockers
    assert result.exits[0].reason == "break_even"


@pytest.mark.parametrize("changes", [
    {"partial_fraction": float("nan")}, {"partial_fraction": float("inf")},
    {"volume_weights": (float("nan"),)},
])
def test_nonfinite_partial_genomes_keep_canonical_value_error(changes):
    genome = partial(**changes)
    ticks, assumptions = tape([100, 101, 102, 103]), execution()
    for run in (lambda: simulate(ticks, genome, execution=assumptions),
                lambda: FastEvaluator(execution=assumptions)(ticks, genome),
                lambda: oracle_simulate(ticks, genome, execution=execution_to_scenario(assumptions))):
        with pytest.raises(ValueError, match="not JSON compliant"):
            run()


@pytest.mark.parametrize("fraction", [0., 1.])
def test_finite_invalid_partial_fraction_preserves_blocked_result(fraction):
    genome = partial(partial_fraction=fraction)
    result = compare(tape([100, 101, 102, 103]), genome)
    assert result.entries == ()
    assert set(genome.validation_errors()) <= set(result.blockers)
