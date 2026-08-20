from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from research.dubai_iterative.contracts import SearchSpace, StrategyGenome
from research.dubai_iterative.dataset import DubaiLeg, DubaiPath, LevelEvent, ProviderEvent
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import (
    ExecutionScenario,
    certify_candidate,
    oracle_simulate,
    stress_candidate,
)
from research.dubai_iterative.evolution import sample_diverse_population, seed_population


BASE = datetime(2026, 7, 27, 9, tzinfo=timezone.utc)


def _frozen(values, dtype=float):
    array = np.asarray(values, dtype=dtype)
    array.setflags(write=False)
    return array


def _leg(
    ticket="101",
    *,
    opened_at=BASE,
    open_price=100.2,
    volume=0.01,
    tp=None,
    sl=None,
    level_at=BASE,
    role="market_a",
):
    return DubaiLeg(
        ticket=str(ticket),
        role=role,
        volume=float(volume),
        opened_at=opened_at,
        open_price=float(open_price),
        closed_at=None,
        close_price=None,
        close_reason=None,
        actual_pnl_eur=Decimal("0"),
        tp_events=() if tp is None else (
            LevelEvent(level_at, float(tp), "confirmed", "provider"),
        ),
        sl_events=() if sl is None else (
            LevelEvent(level_at, float(sl), "confirmed", "provider"),
        ),
    )


def _path(
    bid,
    ask,
    *,
    direction="BUY",
    legs=None,
    provider_events=(),
    interval_seconds=1,
    fx_valid=None,
    conversion_orientation="identity",
):
    count = len(bid)
    legs = tuple(legs or (_leg(open_price=100.2 if direction == "BUY" else 100.0),))
    moments = [BASE + timedelta(seconds=index * interval_seconds) for index in range(count)]
    bid_array = _frozen(bid)
    ask_array = _frozen(ask)
    return DubaiPath(
        signal_id="canal1_oracle",
        day="2026-07-27",
        direction=direction,
        signal_observed_at=BASE,
        opened_at=min(item.opened_at for item in legs),
        actual_pnl_eur=Decimal("0"),
        legs=legs,
        provider_events=tuple(provider_events),
        times_ns=_frozen(
            [int(item.timestamp() * 1_000_000_000) for item in moments],
            dtype=np.int64,
        ),
        bid=bid_array,
        ask=ask_array,
        exit_quotes=bid_array if direction == "BUY" else ask_array,
        fx_bid=_frozen([1.0] * count),
        fx_ask=_frozen([1.0] * count),
        fx_age_ms=_frozen([0.0] * count),
        fx_valid=_frozen([True] * count if fx_valid is None else fx_valid, dtype=bool),
        contract_size=100.0,
        conversion_orientation=conversion_orientation,
        currency_digits=2,
        market_evidence=({"verified": True},),
        conversion_evidence=(),
    )


def _genome(**changes):
    values = {
        "leg_count": 1,
        "volume_weights": (0.01,),
        "target_mode": "none",
        "be_mode": "none",
        "stop_mode": "none",
        "provider_management_mode": "ignore",
        "time_exit_min": 240,
    }
    values.update(changes)
    return StrategyGenome.baseline().with_change(**values)


def _cases():
    return (
        (
            _path([100.0, 101.2], [100.2, 101.4]),
            _genome(target_mode="fixed_basket", target_value=1.0),
        ),
        (
            _path(
                [100.0, 98.8],
                [100.2, 99.0],
                direction="SELL",
                legs=(_leg(open_price=100.0),),
            ),
            _genome(target_mode="fixed_basket", target_value=1.0),
        ),
        (
            _path(
                [100.0, 101.0, 102.0],
                [100.2, 101.2, 102.2],
                legs=(
                    _leg("101", open_price=100.2, tp=101.0),
                    _leg("102", open_price=100.4, tp=102.0, role="scale_out_leg"),
                ),
            ),
            _genome(
                leg_count=2,
                volume_weights=(0.01, 0.01),
                target_mode="provider_per_leg",
            ),
        ),
        (
            _path([100.0, 101.5, 100.2], [100.2, 101.7, 100.4]),
            _genome(be_mode="price", be_trigger=1.0),
        ),
        (
            _path(
                [100.2, 100.7, 101.2, 102.2],
                [100.4, 100.9, 101.4, 102.4],
                legs=(_leg(open_price=100.2, volume=0.02),),
            ),
            _genome(
                volume_weights=(0.02,),
                target_mode="partial_runner",
                target_value=1.0,
                partial_fraction=0.5,
                runner_target=2.5,
            ),
        ),
        (
            _path(
                [100.2, 101.2, 103.2, 104.2, 102.7],
                [100.4, 101.4, 103.4, 104.4, 102.9],
            ),
            _genome(profit_lock_arm=3.0, profit_lock_giveback=1.0),
        ),
        (
            _path(
                [100.2, 100.7, 102.0],
                [100.4, 100.9, 102.2],
                provider_events=(ProviderEvent(
                    BASE + timedelta(seconds=1),
                    "CLOSE_ALL",
                    {},
                ),),
            ),
            _genome(provider_management_mode="exact"),
        ),
        (
            _path([100.0, 100.3, 101.1, 102.1], [100.2, 100.5, 101.3, 102.3]),
            _genome(
                entry_mode="momentum",
                entry_value=1.0,
                entry_expiry_min=1,
                target_mode="fixed_basket",
                target_value=0.8,
            ),
        ),
        (
            _path(
                [4017.49, 4017.49, 4016.97, 4017.49],
                [4017.69, 4017.69, 4017.17, 4017.69],
                direction="SELL",
                interval_seconds=60,
                legs=(
                    _leg("first", open_price=4017.69, role="market_a"),
                    _leg("runner", open_price=4017.69, role="scale_out_leg"),
                ),
            ),
            _genome(
                leg_count=2,
                volume_weights=(0.01, 0.01),
                be_mode="partial",
                be_trigger=0.5,
                time_exit_min=3,
            ),
        ),
        (
            _path(
                [100.0, 99.0, 98.0, 102.0],
                [100.2, 99.2, 98.2, 102.2],
                interval_seconds=60,
            ),
            _genome(
                leg_count=3,
                volume_weights=(0.01, 0.01, 0.01),
                entry_ladder_mode="adverse",
                entry_ladder_step=1.0,
                entry_expiry_min=2,
                time_exit_min=3,
            ),
        ),
        (
            _path(
                [100.0, 99.0, 98.0, 99.8, 99.9],
                [100.2, 99.2, 98.2, 100.0, 100.1],
                interval_seconds=60,
            ),
            _genome(
                leg_count=3,
                volume_weights=(0.01, 0.02, 0.03),
                entry_ladder_mode="adverse",
                entry_ladder_step=1.0,
                entry_expiry_min=2,
                target_mode="fixed_move",
                target_value=1.0,
                time_exit_min=4,
            ),
        ),
    )


def test_oracle_does_not_import_the_fast_engine():
    source = Path("research/dubai_iterative/oracle.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(name.endswith(".engine") for name in imports)


@pytest.mark.parametrize(("path", "genome"), _cases())
def test_independent_oracle_matches_fast_engine_for_strategy_families(path, genome):
    fast = simulate(path, genome)
    oracle = oracle_simulate(path, genome)

    certificate = certify_candidate((path,), genome, (fast,))

    assert oracle.pnl_eur == fast.pnl_eur
    assert oracle.exit_reason == fast.exit_reason
    assert certificate.status == "pass"
    assert certificate.mismatches == ()


@pytest.mark.parametrize(("path", "genome"), _cases())
def test_compiled_research_engine_matches_scalar_engine(path, genome):
    expected = simulate(path, genome)
    actual = FastEvaluator()(path, genome)

    certificate = certify_candidate((path,), genome, (actual,))

    assert actual.pnl_eur == expected.pnl_eur
    assert actual.exit_reason == expected.exit_reason
    assert actual.last_tick_index == expected.last_tick_index
    assert actual.blockers == expected.blockers
    assert certificate.status == "pass"


def test_oracle_matches_exact_decimal_fixed_move_boundary():
    path = _path(
        [4403.99, 4404.00],
        [4404.20, 4404.21],
        legs=(
            _leg("1", open_price=4403.75),
            _leg("2", open_price=4403.49),
            _leg("3", open_price=4403.23),
        ),
    )
    genome = _genome(
        leg_count=3,
        volume_weights=(0.01, 0.01, 0.01),
        target_mode="fixed_move",
        target_value=0.5,
    )

    fast = FastEvaluator()(path, genome)
    independent = oracle_simulate(path, genome)
    certificate = certify_candidate((path,), genome, (fast,))

    assert fast.exits[0].tick_index == 0
    assert independent.exits[0].tick_index == 0
    assert certificate.status == "pass"
    assert certificate.mismatches == ()


def test_oracle_blocks_a_one_cent_disagreement():
    path, genome = _cases()[0]
    fast = simulate(path, genome)
    altered = replace(fast, pnl_eur=fast.pnl_eur + Decimal("0.01"))

    certificate = certify_candidate((path,), genome, (altered,))

    assert certificate.status == "blocked"
    assert any(item.field == "pnl_eur" for item in certificate.mismatches)
    assert certificate.promotion_eligible is False


def test_oracle_never_promotes_matching_but_incomplete_money_evidence():
    path = _path(
        [100.0, 101.2],
        [100.2, 101.4],
        fx_valid=(True, False),
        conversion_orientation="account_base_profit_quote",
    )
    genome = _genome(target_mode="fixed_basket", target_value=1.0)
    fast = simulate(path, genome)

    certificate = certify_candidate((path,), genome, (fast,))

    assert certificate.status == "blocked_evidence"
    assert certificate.mismatches == ()
    assert certificate.promotion_eligible is False


def test_oracle_applies_context_filter_to_observed_entry():
    path = _path([100.0, 101.0], [100.2, 101.2])
    genome = _genome(
        entry_mode="actual_mt5",
        context_filter_mode="max_spread",
        context_filter_value=0.10,
    )

    result = oracle_simulate(path, genome)

    assert result.unfilled is True
    assert result.entries == ()


def test_oracle_cancels_delayed_entry_after_provider_stop_was_hit():
    path = _path(
        [100.0, 89.0, 100.0, 101.0, 102.0],
        [100.2, 89.2, 100.2, 101.2, 102.2],
        legs=(_leg(open_price=100.2, sl=90.0),),
        interval_seconds=60,
    )
    genome = _genome(
        entry_mode="momentum",
        entry_value=1.0,
        entry_expiry_min=5,
        target_mode="fixed_basket",
        target_value=0.5,
        stop_mode="provider",
        time_exit_min=5,
    )

    fast = FastEvaluator()(path, genome)
    oracle = oracle_simulate(path, genome)
    certificate = certify_candidate((path,), genome, (fast,))

    assert fast.unfilled is True
    assert oracle.unfilled is True
    assert certificate.status == "pass"


def test_stress_gate_rejects_a_candidate_that_flips_after_costs():
    path = _path([100.0, 100.8], [100.2, 101.0])
    genome = _genome(target_mode="fixed_basket", target_value=0.5)

    report = stress_candidate(
        (path,),
        genome,
        scenarios=(
            ExecutionScenario(
                name="severe_test",
                latency_ms=0,
                entry_slippage=0.4,
                exit_slippage=0.4,
                spread_addition=0.0,
            ),
        ),
    )

    assert report.base_net_eur > 0
    assert report.scenarios[0].net_eur <= 0 or report.scenarios[0].blockers
    assert report.promotion_eligible is False


def test_oracle_matches_adverse_execution_assumptions():
    path = _path([100.2, 100.7], [100.4, 100.9])
    genome = _genome(target_mode="fixed_basket", target_value=0.4)
    scenario = ExecutionScenario(
        name="slippage",
        entry_slippage=0.0,
        exit_slippage=0.1,
        spread_addition=0.0,
        latency_ms=0,
    )

    fast = simulate(
        path,
        genome,
        execution=ExecutionAssumptions(exit_slippage=0.1),
    )
    independent = oracle_simulate(path, genome, execution=scenario)

    assert independent.pnl_eur == fast.pnl_eur == Decimal("0.40")
    assert independent.exits[0].exit_price == fast.exits[0].exit_price


def test_all_engines_delay_every_provider_artifact_by_execution_latency():
    close = ProviderEvent(BASE + timedelta(seconds=1), "CLOSE_ALL", {})
    path = _path(
        [100.2, 100.7, 101.2, 101.7],
        [100.4, 100.9, 101.4, 101.9],
        provider_events=(close,),
    )
    genome = _genome(provider_management_mode="exact")
    assumptions = ExecutionAssumptions(latency_ms=2_000)
    scenario = ExecutionScenario("latency", latency_ms=2_000)

    scalar = simulate(path, genome, execution=assumptions)
    compiled = FastEvaluator(execution=assumptions)(path, genome)
    independent = oracle_simulate(path, genome, execution=scenario)

    assert scalar.last_tick_index == 3
    assert compiled.last_tick_index == 3
    assert independent.last_tick_index == 3
    assert scalar.pnl_eur == compiled.pnl_eur == independent.pnl_eur == Decimal("1.50")

    certificate = certify_candidate(
        (path,),
        genome,
        (compiled,),
        execution=scenario,
    )
    assert certificate.status == "pass"


@pytest.mark.parametrize(
    ("path", "genome", "expected_index", "expected_pnl"),
    (
        (
            _path(
                [100.2, 101.2, 100.7, 100.8, 101.1],
                [100.4, 101.4, 100.9, 101.0, 101.3],
                legs=(
                    _leg(
                        open_price=100.2,
                        tp=101.0,
                        level_at=BASE + timedelta(seconds=1),
                    ),
                ),
            ),
            _genome(target_mode="provider_per_leg"),
            4,
            Decimal("0.90"),
        ),
        (
            _path(
                [100.2, 99.0, 100.0, 100.1, 98.9],
                [100.4, 99.2, 100.2, 100.3, 99.1],
                legs=(
                    _leg(
                        open_price=100.2,
                        sl=99.5,
                        level_at=BASE + timedelta(seconds=1),
                    ),
                ),
            ),
            _genome(stop_mode="provider"),
            4,
            Decimal("-1.30"),
        ),
        (
            _path(
                [100.2, 101.2, 100.2, 100.8, 100.2],
                [100.4, 101.4, 100.4, 101.0, 100.4],
                provider_events=(
                    ProviderEvent(
                        BASE + timedelta(seconds=1),
                        "MOVE_SL_TO_BE",
                        {},
                    ),
                ),
            ),
            _genome(
                be_mode="provider",
                stop_mode="provider",
                provider_management_mode="exact",
            ),
            4,
            Decimal("0.00"),
        ),
    ),
)
def test_all_engines_share_delayed_tp_sl_and_be_timeline(
    path,
    genome,
    expected_index,
    expected_pnl,
):
    assumptions = ExecutionAssumptions(latency_ms=2_000)
    scenario = ExecutionScenario("latency", latency_ms=2_000)

    scalar = simulate(path, genome, execution=assumptions)
    compiled = FastEvaluator(execution=assumptions)(path, genome)
    independent = oracle_simulate(path, genome, execution=scenario)

    assert scalar.last_tick_index == expected_index
    assert compiled.last_tick_index == expected_index
    assert independent.last_tick_index == expected_index
    assert scalar.pnl_eur == compiled.pnl_eur == independent.pnl_eur == expected_pnl

    certificate = certify_candidate(
        (path,),
        genome,
        (compiled,),
        execution=scenario,
    )
    assert certificate.status == "pass"


def test_compiled_engine_matches_scalar_and_oracle_under_adverse_execution():
    path = _path([100.2, 100.7, 101.2], [100.4, 100.9, 101.4])
    genome = _genome(target_mode="fixed_basket", target_value=0.4)
    assumptions = ExecutionAssumptions(
        entry_slippage=0.1,
        exit_slippage=0.1,
        spread_addition=0.1,
        latency_ms=0,
    )
    scenario = ExecutionScenario(
        "adverse",
        entry_slippage=0.1,
        exit_slippage=0.1,
        spread_addition=0.1,
        latency_ms=0,
    )

    compiled = FastEvaluator(execution=assumptions)(path, genome)
    scalar = simulate(path, genome, execution=assumptions)
    independent = oracle_simulate(path, genome, execution=scenario)

    assert compiled.pnl_eur == scalar.pnl_eur == independent.pnl_eur
    assert compiled.exit_reason == scalar.exit_reason == independent.exit_reason
    assert compiled.exits == scalar.exits

    certificate = certify_candidate(
        (path,),
        genome,
        (compiled,),
        execution=scenario,
    )
    assert certificate.status == "pass"
    assert certificate.mismatches == ()


def test_oracle_matches_the_fast_engine_across_the_complete_seed_grammar():
    legs = tuple(
        _leg(
            str(200 + index),
            open_price=100.2,
            tp=101.0 + index,
            sl=97.0,
            role="market_a" if index == 0 else "scale_out_leg",
        )
        for index in range(4)
    )
    bid = []
    for index in range(301):
        phase = index % 60
        bid.append(round(
            100.2
            + (phase * 0.08 if phase <= 30 else (60 - phase) * 0.08)
            - index * 0.001,
            2,
        ))
    path = _path(
        bid,
        [value + 0.2 for value in bid],
        legs=legs,
        interval_seconds=60,
        provider_events=(
            ProviderEvent(BASE + timedelta(minutes=45), "MOVE_SL_TO_BE", {}),
            ProviderEvent(BASE + timedelta(minutes=180), "CLOSE_ALL", {}),
        ),
    )
    population = seed_population(
        SearchSpace(max_total_volume=0.20, max_legs=12),
        seed=20260817,
    )
    compiled = FastEvaluator()

    for genome in population:
        fast = compiled(path, genome)
        certificate = certify_candidate((path,), genome, (fast,))
        assert certificate.status == "pass", (
            genome.to_dict(),
            certificate.mismatches[:3],
        )


def test_oracle_matches_fast_engine_for_radically_combined_strategies():
    bid = [
        round(100.2 + ((index % 80) - 40) * 0.04, 2)
        for index in range(481)
    ]
    path = _path(
        bid,
        [round(value + 0.2, 2) for value in bid],
        interval_seconds=60,
        provider_events=(
            ProviderEvent(BASE + timedelta(minutes=3), "MOVE_SL_TO_BE", {}),
            ProviderEvent(BASE + timedelta(minutes=5), "CLOSE_ALL", {}),
        ),
    )
    space = SearchSpace(
        max_total_volume=0.50,
        max_legs=12,
        max_entry_expiry_min=120,
        max_time_exit_min=480,
    )
    population = sample_diverse_population(space, seed=731, count=256)
    compiled = FastEvaluator()

    for genome in population:
        fast = compiled(path, genome)
        certificate = certify_candidate((path,), genome, (fast,))
        assert certificate.status == "pass", (
            genome.to_dict(),
            certificate.mismatches[:3],
        )


def test_oracle_matches_compiled_engine_for_radical_strategies_with_costs():
    bid = [
        round(100.2 + ((index % 80) - 40) * 0.04, 2)
        for index in range(241)
    ]
    path = _path(
        bid,
        [round(value + 0.2, 2) for value in bid],
        interval_seconds=60,
        provider_events=(
            ProviderEvent(BASE + timedelta(minutes=3), "MOVE_SL_TO_BE", {}),
            ProviderEvent(BASE + timedelta(minutes=5), "CLOSE_ALL", {}),
        ),
    )
    space = SearchSpace(
        max_total_volume=0.50,
        max_legs=12,
        max_entry_expiry_min=120,
        max_time_exit_min=240,
    )
    population = sample_diverse_population(space, seed=912, count=64)
    assumptions = ExecutionAssumptions(
        entry_slippage=0.05,
        exit_slippage=0.07,
        spread_addition=0.03,
        latency_ms=750,
    )
    scenario = ExecutionScenario(
        "search_costs",
        entry_slippage=0.05,
        exit_slippage=0.07,
        spread_addition=0.03,
        latency_ms=750,
    )
    compiled = FastEvaluator(execution=assumptions)

    for genome in population:
        fast = compiled(path, genome)
        certificate = certify_candidate(
            (path,),
            genome,
            (fast,),
            execution=scenario,
        )
        assert certificate.status == "pass", (
            genome.to_dict(),
            certificate.mismatches[:3],
        )
