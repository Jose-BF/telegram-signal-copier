from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import (
    LevelEvent,
    ProviderEvent,
    SignalLeg,
    SignalPath,
)
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import oracle_simulate
from research.gold_iterative.contracts import (
    gold_555_genome,
    gold_555_until_expiry_genome,
    gold_c490_genome,
)


BASE = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
FAST = FastEvaluator()


def _array(values, dtype=float):
    result = np.asarray(values, dtype=dtype)
    result.setflags(write=False)
    return result


def _path(
    bid,
    ask,
    *,
    direction="BUY",
    interval_seconds=60,
    legs=5,
    provider_events=(),
    tp=None,
    sl=None,
):
    count = len(bid)
    bid_values = _array(bid)
    ask_values = _array(ask)
    leg_rows = tuple(
        SignalLeg(
            ticket=str(20_000 + index),
            role="market_a" if index == 0 else "scale_out_leg",
            volume=0.01,
            opened_at=BASE,
            open_price=100.2 if direction == "BUY" else 100.0,
            closed_at=None,
            close_price=None,
            close_reason=None,
            actual_pnl_eur=Decimal("0"),
            tp_events=(
                ()
                if tp is None
                else (LevelEvent(BASE, float(tp), "confirmed", "provider"),)
            ),
            sl_events=(
                ()
                if sl is None
                else (LevelEvent(BASE, float(sl), "confirmed", "provider"),)
            ),
        )
        for index in range(legs)
    )
    return SignalPath(
        signal_id="canal2_oracle",
        day="2026-08-31",
        direction=direction,
        signal_observed_at=BASE,
        opened_at=BASE,
        actual_pnl_eur=Decimal("0"),
        legs=leg_rows,
        provider_events=tuple(provider_events),
        times_ns=_array(
            [
                int((BASE + timedelta(seconds=index * interval_seconds)).timestamp() * 1_000_000_000)
                for index in range(count)
            ],
            dtype=np.int64,
        ),
        bid=bid_values,
        ask=ask_values,
        exit_quotes=bid_values if direction == "BUY" else ask_values,
        fx_bid=_array([1.0] * count),
        fx_ask=_array([1.0] * count),
        fx_age_ms=_array([0.0] * count),
        fx_valid=_array([True] * count, dtype=bool),
        contract_size=100.0,
        conversion_orientation="identity",
        currency_digits=2,
        market_evidence=({"verified": True},),
        conversion_evidence=(),
    )


def _single_555(**changes):
    values = {
        "entry_ladder_mode": "simultaneous",
        "entry_ladder_step": None,
        "leg_count": 1,
        "volume_weights": (0.04,),
        "target_steps": (0.5,),
        "profit_lock_arm": None,
        "profit_lock_giveback": None,
        "pending_entry_policy": "none",
    }
    values.update(changes)
    return gold_555_genome().with_change(**values)


def _assert_same(scalar, oracle):
    assert oracle.pnl_eur == scalar.pnl_eur
    assert oracle.exit_reason == scalar.exit_reason
    assert oracle.blockers == scalar.blockers
    assert oracle.unfilled == scalar.unfilled
    assert [
        (item.tick_index, item.entry_price, item.volume, item.source)
        for item in oracle.entries
    ] == [
        (item.tick_index, item.entry_price, item.volume, item.source)
        for item in scalar.entries
    ]
    assert [
        (item.tick_index, item.exit_price, item.volume, item.pnl_eur, item.reason)
        for item in oracle.exits
    ] == [
        (item.tick_index, item.exit_price, item.volume, item.pnl_eur, item.reason)
        for item in scalar.exits
    ]


def test_all_engines_reject_actual_entry_on_provider_template() -> None:
    path = replace(
        _path([100.0, 100.5], [100.2, 100.7]),
        entry_evidence_kind="provider_telegram",
    )
    genome = gold_c490_genome().with_change(entry_mode="actual_mt5")

    scalar = simulate(path, genome)
    fast = FAST(path, genome)
    oracle = oracle_simulate(path, genome)

    assert scalar.blockers == ("actual_entry_evidence_missing",)
    _assert_same(scalar, fast)
    _assert_same(scalar, oracle)


def test_all_engines_apply_rollover_cost_before_exit_decisions() -> None:
    lookup = _array([0, -2], dtype=np.int64)
    path = replace(
        _path(
            [100.0, 100.0, 100.0],
            [100.0, 100.0, 100.0],
            interval_seconds=60,
            legs=1,
        ),
        rollover_events=(SimpleNamespace(
            observed_at=BASE + timedelta(minutes=1),
            minor_by_volume_unit=lookup,
            blocker=None,
        ),),
    )
    genome = gold_c490_genome().with_change(
        leg_count=1,
        volume_weights=(0.01,),
        target_mode="none",
        target_steps=(),
        be_mode="none",
        stop_mode="none",
        trailing_distance=None,
        hard_stop_eur_per_leg=None,
        profit_lock_arm=None,
        profit_lock_giveback=None,
        time_exit_min=2,
        time_exit_mode="loss_only",
    )

    scalar = simulate(path, genome)
    fast = FastEvaluator()(path, genome)
    oracle = oracle_simulate(path, genome)

    assert scalar.pnl_eur == Decimal("-0.02")
    assert scalar.exits[0].pnl_eur == Decimal("-0.02")
    assert scalar.exit_reason == "time_exit"
    _assert_same(scalar, fast)
    _assert_same(scalar, oracle)


def test_all_engines_fail_closed_only_when_open_position_reaches_unknown_rollover() -> None:
    path = replace(
        _path(
            [100.0, 100.0, 100.0],
            [100.0, 100.0, 100.0],
            interval_seconds=60,
            legs=1,
        ),
        rollover_events=(SimpleNamespace(
            observed_at=BASE + timedelta(minutes=1),
            minor_by_volume_unit=_array([0, 0], dtype=np.int64),
            blocker="missing_swap_rollover_bracket:fixture",
        ),),
    )
    genome = gold_c490_genome().with_change(
        leg_count=1,
        volume_weights=(0.01,),
        target_mode="none",
        be_mode="none",
        stop_mode="none",
        hard_stop_eur_per_leg=None,
        profit_lock_arm=None,
        profit_lock_giveback=None,
        time_exit_min=2,
        time_exit_mode="loss_only",
    )

    scalar = simulate(path, genome)
    fast = FastEvaluator()(path, genome)
    oracle = oracle_simulate(path, genome)

    assert scalar.pnl_eur is None
    assert scalar.blockers == ("missing_swap_rollover_bracket:fixture",)
    _assert_same(scalar, fast)
    _assert_same(scalar, oracle)


def test_all_engines_do_not_charge_rollover_to_entry_opened_after_event() -> None:
    path = replace(
        _path(
            [100.0, 100.0, 100.0],
            [100.0, 100.0, 100.0],
            interval_seconds=120,
            legs=1,
        ),
        rollover_events=(SimpleNamespace(
            observed_at=BASE + timedelta(minutes=1),
            minor_by_volume_unit=_array([0, -2], dtype=np.int64),
            blocker=None,
        ),),
    )
    genome = _single_555(
        entry_mode="delay",
        entry_value=90.0,
        entry_expiry_min=5,
        volume_weights=(0.01,),
        target_mode="none",
        target_steps=(),
        be_mode="none",
        stop_mode="none",
        trailing_distance=None,
        hard_stop_eur_per_leg=None,
        profit_lock_arm=None,
        profit_lock_giveback=None,
        time_exit_min=2,
        time_exit_mode="none",
    )

    scalar = simulate(path, genome)
    fast = FastEvaluator()(path, genome)
    oracle = oracle_simulate(path, genome)

    assert scalar.entries[0].opened_at == BASE + timedelta(minutes=2)
    assert scalar.pnl_eur == Decimal("0.00")
    _assert_same(scalar, fast)
    _assert_same(scalar, oracle)


@pytest.mark.parametrize(
    ("direction", "bid", "ask", "expected"),
    [
        (
            "BUY",
            [100.0, 99.0, 98.5, 100.0, 100.7],
            [100.2, 99.2, 98.7, 100.2, 100.9],
            Decimal("2.00"),
        ),
        (
            "SELL",
            [100.0, 101.0, 101.5, 100.0, 99.3],
            [100.2, 101.2, 101.7, 100.2, 99.5],
            Decimal("2.00"),
        ),
    ],
)
def test_oracle_independently_matches_555_adverse_reversal(
    direction, bid, ask, expected
) -> None:
    path = _path(bid, ask, direction=direction, legs=1)
    genome = _single_555()

    scalar = simulate(path, genome)
    oracle = oracle_simulate(path, genome)
    fast = FAST(path, genome)

    assert oracle.pnl_eur == expected
    _assert_same(scalar, oracle)
    _assert_same(scalar, fast)


def test_555_entry_expiry_is_anchored_to_telegram_send_time() -> None:
    path = replace(
        _path(
            [100.0, 98.8, 100.4],
            [100.2, 99.0, 100.6],
            interval_seconds=5,
            legs=1,
        ),
        signal_observed_at=BASE + timedelta(seconds=50),
        entry_expiry_anchor_at=BASE,
        times_ns=_array(
            [
                int((BASE + timedelta(seconds=offset)).timestamp() * 1_000_000_000)
                for offset in (50, 55, 65)
            ],
            dtype=np.int64,
        ),
    )
    genome = _single_555(entry_expiry_min=1)

    scalar = simulate(path, genome)
    fast = FastEvaluator()(path, genome)
    oracle = oracle_simulate(path, genome)

    assert scalar.unfilled is True
    assert scalar.entries == ()
    _assert_same(scalar, fast)
    _assert_same(scalar, oracle)


def test_oracle_independently_matches_temporary_flat_555_ladder() -> None:
    path = _path(
        [100.0, 99.0, 98.5, 100.0, 100.7, 98.5, 99.7],
        [100.2, 99.2, 98.7, 100.2, 100.9, 98.7, 99.9],
        legs=2,
    )
    genome = gold_555_until_expiry_genome().with_change(
        leg_count=2,
        volume_weights=(0.04, 0.03),
        target_steps=(0.5, 1.0),
        profit_lock_arm=None,
        profit_lock_giveback=None,
    )

    scalar = simulate(path, genome)
    oracle = oracle_simulate(path, genome)
    fast = FAST(path, genome)

    assert oracle.pnl_eur == Decimal("5.00")
    assert [item.tick_index for item in oracle.entries] == [3, 5]
    _assert_same(scalar, oracle)
    _assert_same(scalar, fast)


def test_oracle_independently_matches_monotonic_trailing_stop() -> None:
    path = _path(
        [100.0, 105.0, 104.0, 101.9],
        [100.2, 105.2, 104.2, 102.1],
        legs=1,
    )
    genome = _single_555(
        entry_mode="signal_market",
        entry_value=None,
        entry_confirmation_value=None,
        target_mode="none",
        target_steps=(),
        trailing_distance=3.0,
    )

    scalar = simulate(path, genome)
    oracle = oracle_simulate(path, genome)
    fast = FAST(path, genome)

    assert oracle.exit_reason == "trailing_stop"
    assert oracle.pnl_eur == Decimal("6.80")
    _assert_same(scalar, oracle)
    _assert_same(scalar, fast)


def test_oracle_independently_matches_c490_per_leg_hard_stop() -> None:
    path = _path([100.0, 80.2], [100.2, 80.4])
    genome = gold_c490_genome().with_change(stop_value=200.0)

    scalar = simulate(path, genome)
    oracle = oracle_simulate(path, genome)
    fast = FAST(path, genome)

    assert oracle.pnl_eur == Decimal("-100.00")
    assert {item.reason for item in oracle.exits} == {"hard_stop_per_leg"}
    _assert_same(scalar, oracle)
    _assert_same(scalar, fast)


def test_three_engines_match_555_profit_lock_at_exact_money_boundary() -> None:
    path = _path(
        [100.0, 107.7, 107.45],
        [100.2, 107.9, 107.65],
        legs=1,
    )
    genome = _single_555(
        entry_mode="signal_market",
        entry_value=None,
        entry_confirmation_value=None,
        target_mode="none",
        target_steps=(),
        profit_lock_arm=30.0,
        profit_lock_giveback=1.0,
    )

    scalar = simulate(path, genome)
    oracle = oracle_simulate(path, genome)
    fast = FAST(path, genome)

    assert scalar.exit_reason == "profit_lock"
    assert scalar.pnl_eur == Decimal("29.00")
    _assert_same(scalar, oracle)
    _assert_same(scalar, fast)


def test_three_engines_match_555_non_negative_time_exit() -> None:
    path = _path(
        [100.0, 99.0, 100.2],
        [100.2, 99.2, 100.4],
        legs=1,
    )
    genome = _single_555(
        entry_mode="signal_market",
        entry_value=None,
        entry_confirmation_value=None,
        target_mode="none",
        target_steps=(),
        profit_lock_arm=None,
        profit_lock_giveback=None,
        time_exit_min=2,
    )

    scalar = simulate(path, genome)
    oracle = oracle_simulate(path, genome)
    fast = FAST(path, genome)

    assert scalar.exit_reason == "time_exit"
    assert scalar.pnl_eur == Decimal("0.00")
    _assert_same(scalar, oracle)
    _assert_same(scalar, fast)


def test_three_engines_match_555_explicit_provider_close() -> None:
    close_at = BASE + timedelta(minutes=1)
    path = _path(
        [100.0, 101.0],
        [100.2, 101.2],
        legs=1,
        provider_events=(
            ProviderEvent(close_at, "CLOSE_ALL", {"raw_text": "Close now"}),
        ),
    )
    genome = _single_555(
        entry_mode="signal_market",
        entry_value=None,
        entry_confirmation_value=None,
        target_mode="none",
        target_steps=(),
        profit_lock_arm=None,
        profit_lock_giveback=None,
    )

    scalar = simulate(path, genome)
    oracle = oracle_simulate(path, genome)
    fast = FAST(path, genome)

    assert scalar.exit_reason == "provider_close"
    assert scalar.pnl_eur == Decimal("3.20")
    _assert_same(scalar, oracle)
    _assert_same(scalar, fast)


def test_three_engines_ignore_partial_close_for_explicit_full_close_mode() -> None:
    partial_at = BASE + timedelta(minutes=1)
    path = _path(
        [100.0, 100.1, 100.7],
        [100.2, 100.3, 100.9],
        legs=1,
        provider_events=(
            ProviderEvent(
                partial_at,
                "CLOSE_PARTIAL",
                {"raw_text": "I will close partials"},
            ),
        ),
    )
    genome = _single_555(
        entry_mode="signal_market",
        entry_value=None,
        entry_confirmation_value=None,
    )

    scalar = simulate(path, genome)
    oracle = oracle_simulate(path, genome)
    fast = FAST(path, genome)

    assert scalar.exit_reason == "per_leg_target"
    assert scalar.pnl_eur == Decimal("2.00")
    _assert_same(scalar, oracle)
    _assert_same(scalar, fast)


def test_three_engines_fill_per_leg_target_at_limit_not_overshoot() -> None:
    path = _path(
        [100.0, 101.4],
        [100.2, 101.6],
        legs=1,
    )
    genome = _single_555(
        entry_mode="signal_market",
        entry_value=None,
        entry_confirmation_value=None,
    )

    scalar = simulate(path, genome)
    oracle = oracle_simulate(path, genome)
    fast = FAST(path, genome)

    assert scalar.exit_reason == "per_leg_target"
    assert scalar.exits[0].exit_price == 100.7
    assert scalar.pnl_eur == Decimal("2.00")
    _assert_same(scalar, oracle)
    _assert_same(scalar, fast)


def test_three_engines_match_c490_break_even_priority() -> None:
    path = _path(
        [100.0, 112.2, 100.2],
        [100.2, 112.4, 100.4],
    )
    genome = gold_c490_genome()

    scalar = simulate(path, genome)
    oracle = oracle_simulate(path, genome)
    fast = FAST(path, genome)

    assert scalar.exit_reason == "break_even"
    assert scalar.pnl_eur == Decimal("0.00")
    _assert_same(scalar, oracle)
    _assert_same(scalar, fast)


def test_three_engines_match_c490_loss_only_time_exit() -> None:
    path = _path(
        [100.0, 100.0, 99.0],
        [100.2, 100.2, 99.2],
    )
    genome = gold_c490_genome().with_change(
        be_mode="none",
        be_trigger=None,
        stop_value=1_000.0,
        hard_stop_eur_per_leg=1_000.0,
        profit_lock_arm=None,
        profit_lock_giveback=None,
        time_exit_min=2,
    )

    scalar = simulate(path, genome)
    oracle = oracle_simulate(path, genome)
    fast = FAST(path, genome)

    assert scalar.exit_reason == "time_exit"
    assert scalar.pnl_eur == Decimal("-6.00")
    _assert_same(scalar, oracle)
    _assert_same(scalar, fast)


def test_three_engines_keep_stop_priority_on_same_tick_ambiguity() -> None:
    path = _path([101.0], [101.2], legs=1, tp=101.0, sl=101.5)
    genome = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.01,),
        be_mode="none",
        provider_management_mode="ignore",
    )

    scalar = simulate(path, genome)
    oracle = oracle_simulate(path, genome)
    fast = FAST(path, genome)

    assert scalar.exit_reason == "provider_sl"
    assert scalar.exits[0].reason == "provider_sl"
    assert scalar.pnl_eur == Decimal("0.80")
    _assert_same(scalar, oracle)
    _assert_same(scalar, fast)
