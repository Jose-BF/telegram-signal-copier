from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import SignalLeg, SignalPath
from research.dubai_iterative.engine import simulate
from research.gold_iterative.contracts import gold_555_genome, gold_c490_genome


BASE = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)


def _frozen(values, dtype=float):
    array = np.asarray(values, dtype=dtype)
    array.setflags(write=False)
    return array


def _leg(index: int, *, direction: str = "BUY") -> SignalLeg:
    return SignalLeg(
        ticket=str(10_000 + index),
        role="market_a" if index == 0 else "scale_out_leg",
        volume=0.01,
        opened_at=BASE,
        open_price=100.2 if direction == "BUY" else 100.0,
        closed_at=None,
        close_price=None,
        close_reason=None,
        actual_pnl_eur=Decimal("0"),
        tp_events=(),
        sl_events=(),
    )


def _path(
    bid,
    ask,
    *,
    direction: str = "BUY",
    interval_seconds: int = 60,
    legs: int = 5,
) -> SignalPath:
    count = len(bid)
    times = [
        BASE + timedelta(seconds=index * interval_seconds)
        for index in range(count)
    ]
    bid_array = _frozen(bid)
    ask_array = _frozen(ask)
    return SignalPath(
        signal_id="canal2_test",
        day="2026-08-31",
        direction=direction,
        signal_observed_at=BASE,
        opened_at=BASE,
        actual_pnl_eur=Decimal("0"),
        legs=tuple(_leg(index, direction=direction) for index in range(legs)),
        provider_events=(),
        times_ns=_frozen(
            [int(moment.timestamp() * 1_000_000_000) for moment in times],
            dtype=np.int64,
        ),
        bid=bid_array,
        ask=ask_array,
        exit_quotes=bid_array if direction == "BUY" else ask_array,
        fx_bid=_frozen([1.0] * count),
        fx_ask=_frozen([1.0] * count),
        fx_age_ms=_frozen([0.0] * count),
        fx_valid=_frozen([True] * count, dtype=bool),
        contract_size=100.0,
        conversion_orientation="identity",
        currency_digits=2,
        market_evidence=({"verified": True},),
        conversion_evidence=(),
    )


def _single_555(**changes) -> StrategyGenome:
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


def test_555_buy_waits_for_adverse_move_and_reversal_before_entry() -> None:
    path = _path(
        [100.0, 99.0, 98.5, 100.0, 100.7],
        [100.2, 99.2, 98.7, 100.2, 100.9],
        legs=1,
    )

    result = simulate(path, _single_555())

    assert [(entry.tick_index, entry.entry_price, entry.source) for entry in result.entries] == [
        (3, 100.2, "causal_adverse_reversal")
    ]
    assert result.exit_reason == "per_leg_target"
    assert result.exits[0].tick_index == 4
    assert result.pnl_eur == Decimal("2.00")


def test_555_sell_is_the_exact_directional_mirror() -> None:
    path = _path(
        [100.0, 101.0, 101.5, 100.0, 99.3],
        [100.2, 101.2, 101.7, 100.2, 99.5],
        direction="SELL",
        legs=1,
    )

    result = simulate(path, _single_555())

    assert [(entry.tick_index, entry.entry_price) for entry in result.entries] == [
        (3, 100.0)
    ]
    assert result.exit_reason == "per_leg_target"
    assert result.exits[0].exit_price == 99.5
    assert result.pnl_eur == Decimal("2.00")


def test_555_temporary_flat_basket_can_fill_a_later_pending_leg() -> None:
    path = _path(
        [100.0, 99.0, 98.5, 100.0, 100.7, 98.5, 99.7],
        [100.2, 99.2, 98.7, 100.2, 100.9, 98.7, 99.9],
        legs=2,
    )
    genome = gold_555_genome().with_change(
        leg_count=2,
        volume_weights=(0.04, 0.03),
        target_steps=(0.5, 1.0),
        profit_lock_arm=None,
        profit_lock_giveback=None,
    )

    result = simulate(path, genome)

    assert [(entry.tick_index, entry.entry_price) for entry in result.entries] == [
        (3, 100.2),
        (5, 98.7),
    ]
    assert [(exit.tick_index, exit.reason) for exit in result.exits] == [
        (4, "per_leg_target"),
        (6, "per_leg_target"),
    ]
    assert result.pnl_eur == Decimal("5.00")


def test_555_ladder_expiry_remains_anchored_to_original_signal() -> None:
    path = _path(
        [100.0, 99.0, 98.5, 100.0, 100.7, 98.5],
        [100.2, 99.2, 98.7, 100.2, 100.9, 98.7],
        interval_seconds=10 * 60,
        legs=2,
    )
    genome = gold_555_genome().with_change(
        leg_count=2,
        volume_weights=(0.04, 0.03),
        target_steps=(0.5, 1.0),
        profit_lock_arm=None,
        profit_lock_giveback=None,
    )

    result = simulate(path, genome)

    # First fill occurs exactly at minute 30; the later minute-50 ladder touch
    # cannot extend the original signal's 30-minute entry window.
    assert result.entries == ()
    assert result.unfilled is True


def test_555_trailing_stop_tightens_and_never_loosens() -> None:
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

    result = simulate(path, genome)

    assert result.exit_reason == "trailing_stop"
    assert result.exits[0].tick_index == 3
    assert result.exits[0].exit_price == 101.9
    assert result.pnl_eur == Decimal("6.80")


def test_c490_signal_market_uses_executable_quote_and_hard_stop_per_leg() -> None:
    path = _path(
        [100.0, 80.2],
        [100.2, 80.4],
    )
    genome = gold_c490_genome().with_change(stop_value=200.0)

    result = simulate(path, genome)

    assert len(result.entries) == 5
    assert {entry.entry_price for entry in result.entries} == {100.2}
    assert {entry.source for entry in result.entries} == {"causal_signal_market"}
    assert len(result.exits) == 5
    assert {exit.reason for exit in result.exits} == {"hard_stop_per_leg"}
    assert result.exit_reason == "hard_stop_per_leg"
    assert result.pnl_eur == Decimal("-100.00")


def test_c490_break_even_is_checked_before_profit_giveback() -> None:
    path = _path(
        [100.0, 112.2, 100.2],
        [100.2, 112.4, 100.4],
    )

    result = simulate(path, gold_c490_genome())

    assert len(result.exits) == 5
    assert {exit.reason for exit in result.exits} == {"break_even"}
    assert result.exit_reason == "break_even"
    assert result.pnl_eur == Decimal("0.00")


def test_c490_loss_only_time_exit_does_not_require_provider_management() -> None:
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

    result = simulate(path, genome)

    assert result.exit_reason == "time_exit"
    assert {exit.reason for exit in result.exits} == {"time_exit"}
    assert result.pnl_eur == Decimal("-6.00")


def test_no_entry_control_is_an_explicit_zero_participation_strategy() -> None:
    path = _path([100.0, 110.0], [100.2, 110.2], legs=1)
    genome = _single_555(
        entry_mode="no_entry",
        entry_value=None,
        entry_confirmation_value=None,
        target_mode="none",
        target_steps=(),
        trailing_distance=None,
    )

    result = simulate(path, genome)

    assert result.unfilled is True
    assert result.entries == ()
    assert result.exits == ()
    assert result.pnl_eur == Decimal("0.00")
