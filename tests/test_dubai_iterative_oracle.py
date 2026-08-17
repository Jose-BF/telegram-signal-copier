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
from research.dubai_iterative.oracle import (
    ExecutionScenario,
    certify_candidate,
    oracle_simulate,
    stress_candidate,
)
from research.dubai_iterative.evolution import seed_population


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


def test_oracle_blocks_a_one_cent_disagreement():
    path, genome = _cases()[0]
    fast = simulate(path, genome)
    altered = replace(fast, pnl_eur=fast.pnl_eur + Decimal("0.01"))

    certificate = certify_candidate((path,), genome, (altered,))

    assert certificate.status == "blocked"
    assert any(item.field == "pnl_eur" for item in certificate.mismatches)
    assert certificate.promotion_eligible is False


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
        bid.append(100.2 + (phase * 0.08 if phase <= 30 else (60 - phase) * 0.08) - index * 0.001)
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

    for genome in population:
        fast = simulate(path, genome)
        certificate = certify_candidate((path,), genome, (fast,))
        assert certificate.status == "pass", (
            genome.to_dict(),
            certificate.mismatches[:3],
        )
