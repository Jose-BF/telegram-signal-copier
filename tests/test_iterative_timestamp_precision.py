"""Causal boundary regressions independent of floating epoch conversion."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import ProviderEvent, SignalLeg, SignalPath
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import ExecutionScenario, oracle_simulate
from research.gold_iterative.contracts import gold_555_genome


BASE = datetime(2026, 9, 7, 10, tzinfo=timezone.utc)
BASE_NS = 1_788_775_200_000_000_000


def _readonly(values, dtype):
    array = np.asarray(values, dtype=dtype)
    array.setflags(write=False)
    return array


def _path(direction, *, event_microseconds=2000, observed_entry=False):
    opened_at = BASE + timedelta(microseconds=2000) if observed_entry else BASE
    quotes = [100.00, 100.10, 99.00] if direction == "BUY" else [100.20, 100.10, 101.20]
    bid = quotes if direction == "BUY" else [value - 0.2 for value in quotes]
    ask = [value + 0.2 for value in quotes] if direction == "BUY" else quotes
    leg = SignalLeg(
        ticket="101", role="market_a", volume=0.04, opened_at=opened_at,
        open_price=100.2 if direction == "BUY" else 100.0,
        closed_at=None, close_price=None, close_reason=None,
        actual_pnl_eur=Decimal(0), tp_events=(), sl_events=(),
    )
    return SignalPath(
        signal_id="canal2_timestamp", day="2026-09-07", direction=direction,
        signal_observed_at=opened_at, opened_at=opened_at, actual_pnl_eur=Decimal(0),
        legs=(leg,), provider_events=() if observed_entry else (
            ProviderEvent(BASE + timedelta(microseconds=event_microseconds), "CLOSE_ALL", {}),
        ),
        times_ns=_readonly([BASE_NS, BASE_NS + 2_000_000, BASE_NS + 3_000_000], np.int64),
        bid=_readonly(bid, float), ask=_readonly(ask, float),
        exit_quotes=_readonly(quotes, float),
        fx_bid=_readonly([1, 1, 1], float), fx_ask=_readonly([1, 1, 1], float),
        fx_age_ms=_readonly([0, 0, 0], np.int64), fx_valid=_readonly([True] * 3, np.bool_),
        contract_size=100, conversion_orientation="identity", currency_digits=2,
        market_evidence=({},), conversion_evidence=({},), entry_evidence_kind="actual_mt5",
    )


def _genome():
    return StrategyGenome(
        entry_mode="actual_mt5", leg_count=1, volume_weights=(0.04,),
        entry_ladder_mode="simultaneous", target_mode="none", stop_mode="none",
        be_mode="none", time_exit_min=180, provider_management_mode="exact",
    )


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_provider_close_on_exact_tick_is_not_delayed_by_epoch_rounding(direction, engine):
    path = _path(direction)
    runner = {"scalar": simulate, "fast": FastEvaluator(), "oracle": oracle_simulate}[engine]
    result = runner(path, _genome())
    assert result.blockers == ()
    assert result.exit_reason == "provider_close"
    assert result.exits[0].tick_index == 1
    assert result.exits[0].closed_at == BASE + timedelta(microseconds=2000)
    assert result.pnl_eur == Decimal("-0.40")


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_observed_entry_on_exact_tick_keeps_its_first_eligible_tick(direction, engine):
    path = _path(direction, observed_entry=True)
    runner = {"scalar": simulate, "fast": FastEvaluator(), "oracle": oracle_simulate}[engine]
    result = runner(path, _genome())
    assert result.entries[0].tick_index == 1
    assert result.entries[0].opened_at == BASE + timedelta(microseconds=2000)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
@pytest.mark.parametrize(("available_us", "latency_ms", "expected_index"), [
    (1999, 0, 1), (2000, 0, 1), (2001, 0, 2), (2000, 1, 2), (1000, 1, 1),
])
def test_close_availability_and_latency_preserve_causal_order(
    direction, engine, available_us, latency_ms, expected_index,
):
    path = _path(direction, event_microseconds=available_us)
    if engine == "oracle":
        result = oracle_simulate(path, _genome(), execution=ExecutionScenario(latency_ms=latency_ms))
    elif engine == "fast":
        result = FastEvaluator(execution=ExecutionAssumptions(latency_ms=latency_ms))(path, _genome())
    else:
        result = simulate(path, _genome(), execution=ExecutionAssumptions(latency_ms=latency_ms))
    assert result.exits[0].tick_index == expected_index
    assert result.blockers == ()


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_timezone_representation_does_not_change_exit_decision(engine):
    path = _path("BUY")
    event = path.provider_events[0]
    local = event.observed_at.astimezone(timezone(timedelta(hours=5, minutes=30)))
    path = replace(path, provider_events=(replace(event, observed_at=local),))
    runner = {"scalar": simulate, "fast": FastEvaluator(), "oracle": oracle_simulate}[engine]
    result = runner(path, _genome())
    assert result.exits[0].tick_index == 1
    assert result.pnl_eur == Decimal("-0.40")


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
@pytest.mark.parametrize("gap", [False, True])
def test_full_gold_basket_stop_preserves_all_losses_and_adverse_gaps(direction, engine, gap):
    path = _path(direction)
    distance = 31 if gap else 30
    entry_price = path.legs[0].open_price
    stop_quote = entry_price + (-distance if direction == "BUY" else distance)
    quotes = [float(path.exit_quotes[0]), stop_quote, stop_quote]
    bid = quotes if direction == "BUY" else [value - 0.2 for value in quotes]
    ask = [value + 0.2 for value in quotes] if direction == "BUY" else quotes
    volumes = (0.04, 0.03, 0.03, 0.03, 0.03)
    path = replace(
        path, provider_events=(), bid=_readonly(bid, float), ask=_readonly(ask, float),
        exit_quotes=_readonly(quotes, float),
        legs=tuple(replace(path.legs[0], ticket=str(101 + index), volume=volume)
                   for index, volume in enumerate(volumes)),
    )
    genome = gold_555_genome().with_change(
        entry_mode="actual_mt5", entry_value=None, entry_confirmation_value=None,
        entry_ladder_mode="simultaneous", entry_ladder_step=None,
    )
    runner = {"scalar": simulate, "fast": FastEvaluator(), "oracle": oracle_simulate}[engine]
    result = runner(path, genome)
    assert result.blockers == ()
    assert len(result.entries) == len(result.exits) == 5
    assert {row.ticket for row in result.entries} == {row.ticket for row in result.exits}
    assert all(row.reason == "trailing_stop" for row in result.exits)
    assert all(row.exit_price == stop_quote for row in result.exits)
    expected = -(Decimal(distance) * Decimal("0.16") * 100)
    assert result.pnl_eur == expected
    assert sum((row.pnl_eur for row in result.exits), Decimal(0)) == expected


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_market_path_ending_with_open_position_cannot_be_certified(engine):
    path = replace(_path("BUY"), provider_events=())
    runner = {"scalar": simulate, "fast": FastEvaluator(), "oracle": oracle_simulate}[engine]
    result = runner(path, _genome())
    assert result.exit_reason == "data_end"
    assert "path_ended_before_strategy_exit" in result.blockers
