from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np

from research.dubai_iterative.contracts import SearchSpace, StrategyGenome
from research.dubai_iterative.dataset import DubaiLeg, DubaiPath
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.evolution import seed_population
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import certify_candidate, oracle_simulate


BASE = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)


def _frozen(values, dtype=float):
    result = np.asarray(values, dtype=dtype)
    result.setflags(write=False)
    return result


def _characterization_path() -> DubaiPath:
    bid = _frozen((100.0, 99.0, 98.0, 99.8, 99.9))
    ask = _frozen((100.2, 99.2, 98.2, 100.0, 100.1))
    times = tuple(BASE + timedelta(minutes=index) for index in range(len(bid)))
    leg = DubaiLeg(
        ticket="101",
        role="market_a",
        volume=0.01,
        opened_at=BASE,
        open_price=100.2,
        closed_at=None,
        close_price=None,
        close_reason=None,
        actual_pnl_eur=Decimal("0.00"),
        tp_events=(),
        sl_events=(),
    )
    return DubaiPath(
        signal_id="canal1_compatibility",
        day="2026-07-27",
        direction="BUY",
        signal_observed_at=BASE,
        opened_at=BASE,
        actual_pnl_eur=Decimal("0.00"),
        legs=(leg,),
        provider_events=(),
        times_ns=_frozen(
            tuple(int(moment.timestamp() * 1_000_000_000) for moment in times),
            dtype=np.int64,
        ),
        bid=bid,
        ask=ask,
        exit_quotes=bid,
        fx_bid=_frozen((1.0,) * len(bid)),
        fx_ask=_frozen((1.0,) * len(bid)),
        fx_age_ms=_frozen((0.0,) * len(bid)),
        fx_valid=_frozen((True,) * len(bid), dtype=bool),
        contract_size=100.0,
        conversion_orientation="identity",
        currency_digits=2,
        market_evidence=({"verified": True},),
        conversion_evidence=(),
    )


def _characterization_genome() -> StrategyGenome:
    return StrategyGenome.baseline().with_change(
        leg_count=3,
        volume_weights=(0.01, 0.02, 0.03),
        entry_expiry_min=2,
        entry_ladder_mode="adverse",
        entry_ladder_step=1.0,
        target_mode="fixed_move",
        target_value=1.0,
        be_mode="none",
        stop_mode="none",
        provider_management_mode="ignore",
        time_exit_min=4,
    )


def test_legacy_dubai_genome_identities_are_frozen():
    baseline = StrategyGenome.baseline()
    fixed_basket = baseline.with_change(
        target_mode="fixed_basket",
        target_value=10.0,
    )

    assert baseline.fingerprint == (
        "544b846be69936c177008f456d1118838427209a567be18b534bd18787669cbe"
    )
    assert fixed_basket.fingerprint == (
        "a111af5c30b17eb2561ea85b0e067ab54779be66d0ab1c8fbc532f7a9b272976"
    )
    assert _characterization_genome().fingerprint == (
        "3f48b2cef331ce3a7120fd29f0f54d9920a93492215da0b9730b47084a192257"
    )


def test_legacy_seed_population_boundary_is_frozen():
    population = seed_population(
        SearchSpace(max_total_volume=0.20, max_legs=12),
        seed=20260817,
    )

    assert len(population) == 159
    assert population[0].fingerprint == (
        "c5b7a4c27a3287adfab3f9f55a3035fa45ffc4bca11dd1318f8bdfa0f9d1dc05"
    )
    assert population[-1].fingerprint == (
        "b707ec62c2fc12b79d702b3a17737856691944da374ff386ae8674f65781495b"
    )


def test_legacy_dubai_engines_remain_cent_exact():
    path = _characterization_path()
    genome = _characterization_genome()

    scalar = simulate(path, genome)
    compiled = FastEvaluator()(path, genome)
    independent = oracle_simulate(path, genome)
    certificate = certify_candidate((path,), genome, (compiled,))

    assert scalar.pnl_eur == compiled.pnl_eur == independent.pnl_eur == Decimal("6.20")
    assert scalar.exit_reason == compiled.exit_reason == independent.exit_reason == (
        "fixed_move_target"
    )
    assert [entry.entry_price for entry in scalar.entries] == [100.2, 99.2, 98.2]
    assert scalar.last_tick_index == compiled.last_tick_index == independent.last_tick_index == 4
    assert certificate.status == "pass"
    assert certificate.mismatches == ()
