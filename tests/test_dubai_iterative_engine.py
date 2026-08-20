from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import (
    DubaiLeg,
    DubaiPath,
    LevelEvent,
    ProviderEvent,
)
from research.dubai_iterative.engine import ExecutionAssumptions, simulate


BASE = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)


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
    tp_events = () if tp is None else (
        LevelEvent(level_at, float(tp), "confirmed", "provider"),
    )
    sl_events = () if sl is None else (
        LevelEvent(level_at, float(sl), "confirmed", "provider"),
    )
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
        tp_events=tp_events,
        sl_events=sl_events,
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
    times = [BASE + timedelta(seconds=index * interval_seconds) for index in range(count)]
    times_ns = _frozen(
        [int(moment.timestamp() * 1_000_000_000) for moment in times],
        dtype=np.int64,
    )
    bid_array = _frozen(bid)
    ask_array = _frozen(ask)
    valid = _frozen(
        [True] * count if fx_valid is None else fx_valid,
        dtype=bool,
    )
    return DubaiPath(
        signal_id="canal1_test",
        day="2026-07-27",
        direction=direction,
        signal_observed_at=BASE,
        opened_at=min(leg.opened_at for leg in (legs or (_leg(),))),
        actual_pnl_eur=Decimal("0"),
        legs=tuple(legs or (_leg(open_price=100.2 if direction == "BUY" else 100.0),)),
        provider_events=tuple(provider_events),
        times_ns=times_ns,
        bid=bid_array,
        ask=ask_array,
        exit_quotes=bid_array if direction == "BUY" else ask_array,
        fx_bid=_frozen([1.0] * count),
        fx_ask=_frozen([1.0] * count),
        fx_age_ms=_frozen([0.0] * count),
        fx_valid=valid,
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


def test_buy_and_sell_use_executable_quote_side():
    buy = simulate(
        _path([100.0, 101.2], [100.2, 101.4]),
        _genome(target_mode="fixed_basket", target_value=1.0),
    )
    sell = simulate(
        _path(
            [100.0, 98.8],
            [100.2, 99.0],
            direction="SELL",
            legs=(_leg(open_price=100.0),),
        ),
        _genome(target_mode="fixed_basket", target_value=1.0),
    )

    assert buy.pnl_eur == Decimal("1.00")
    assert buy.exits[0].exit_price == 101.2
    assert sell.pnl_eur == Decimal("1.00")
    assert sell.exits[0].exit_price == 99.0


def test_fixed_move_target_closes_from_volume_weighted_entry_price():
    path = _path(
        [100.0, 99.0, 98.0, 99.8, 99.9],
        [100.2, 99.2, 98.2, 100.0, 100.1],
        interval_seconds=60,
    )
    genome = _genome(
        leg_count=3,
        volume_weights=(0.01, 0.02, 0.03),
        entry_ladder_mode="adverse",
        entry_ladder_step=1.0,
        entry_expiry_min=2,
        target_mode="fixed_move",
        target_value=1.0,
        time_exit_min=4,
    )

    result = simulate(path, genome)

    assert [entry.entry_price for entry in result.entries] == [100.2, 99.2, 98.2]
    assert result.exit_reason == "fixed_move_target"
    assert result.exits[0].tick_index == 4
    assert result.pnl_eur == Decimal("6.20")


def test_fixed_move_target_hits_exact_decimal_boundary():
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

    result = simulate(path, genome)

    assert result.exit_reason == "fixed_move_target"
    assert result.exits[0].tick_index == 0


def test_adverse_entry_ladder_fills_each_leg_only_after_its_price_level():
    path = _path(
        [100.0, 99.0, 98.0, 102.0],
        [100.2, 99.2, 98.2, 102.2],
        interval_seconds=60,
    )
    genome = _genome(
        leg_count=3,
        volume_weights=(0.01, 0.01, 0.01),
        entry_ladder_mode="adverse",
        entry_ladder_step=1.0,
        entry_expiry_min=2,
        time_exit_min=3,
    )

    result = simulate(path, genome)

    assert [entry.tick_index for entry in result.entries] == [0, 1, 2]
    assert [entry.entry_price for entry in result.entries] == [100.2, 99.2, 98.2]
    assert result.filled_volume == 0.03
    assert result.pnl_eur == Decimal("8.40")
    assert result.confidence_layer == "counterfactual_entry"


def test_counterfactual_entry_is_cancelled_after_provider_stop_was_hit():
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

    result = simulate(path, genome)

    assert result.unfilled is True
    assert result.entries == ()
    assert result.pnl_eur == Decimal("0.00")


def test_adverse_ladder_does_not_add_a_leg_beyond_provider_stop():
    path = _path(
        [100.0, 99.0, 98.0],
        [100.2, 99.2, 98.2],
        legs=(_leg(open_price=100.2, sl=100.0),),
        interval_seconds=60,
    )
    genome = _genome(
        leg_count=3,
        volume_weights=(0.01, 0.01, 0.01),
        entry_ladder_mode="adverse",
        entry_ladder_step=1.0,
        entry_expiry_min=2,
        stop_mode="provider",
        time_exit_min=2,
    )

    result = simulate(path, genome)

    assert len(result.entries) == 1
    assert result.entries[0].entry_price == 100.2
    assert result.exit_reason == "provider_sl"


def test_provider_targets_respect_each_leg_fill_and_level():
    path = _path(
        [100.0, 101.0, 102.0],
        [100.2, 101.2, 102.2],
        legs=(
            _leg("101", open_price=100.2, tp=101.0),
            _leg("102", open_price=100.4, tp=102.0),
        ),
    )
    genome = _genome(
        leg_count=2,
        volume_weights=(0.01, 0.01),
        target_mode="provider_per_leg",
    )

    result = simulate(path, genome)

    assert [item.ticket for item in result.exits] == ["101", "102"]
    assert [item.reason for item in result.exits] == ["provider_tp", "provider_tp"]
    assert result.pnl_eur == Decimal("2.40")


def test_same_tick_provider_stop_precedes_provider_target():
    path = _path(
        [100.2, 100.0],
        [100.4, 100.2],
        legs=(
            _leg(
                open_price=100.2,
                tp=100.0,
                sl=100.0,
                level_at=BASE + timedelta(seconds=1),
            ),
        ),
    )

    result = simulate(
        path,
        _genome(target_mode="provider_per_leg", stop_mode="provider"),
    )

    assert result.exit_reason == "provider_sl"
    assert result.exits[0].reason == "provider_sl"


def test_price_be_closes_reversal_at_entry():
    path = _path(
        [100.0, 101.5, 100.2],
        [100.2, 101.7, 100.4],
    )

    result = simulate(path, _genome(be_mode="price", be_trigger=1.0))

    assert result.exit_reason == "break_even"
    assert result.exits[0].exit_price == 100.2
    assert result.pnl_eur == Decimal("0.00")


def test_partial_be_triggers_on_exact_decimal_sell_move():
    path = _path(
        [4017.99, 4017.49, 4017.99],
        [4018.19, 4017.69, 4018.19],
        direction="SELL",
        interval_seconds=60,
        legs=(
            _leg("first", open_price=4018.19, role="market_a"),
            _leg("runner", open_price=4018.19, role="scale_out_leg"),
        ),
    )
    genome = _genome(
        leg_count=2,
        volume_weights=(0.01, 0.01),
        be_mode="partial",
        be_trigger=0.5,
        time_exit_min=2,
    )

    result = simulate(path, genome)

    runner = next(item for item in result.exits if item.ticket == "runner")
    assert runner.reason == "break_even"
    assert runner.tick_index == 2


def test_profit_lock_closes_after_measured_giveback():
    path = _path(
        [100.2, 101.2, 103.2, 104.2, 102.7],
        [100.4, 101.4, 103.4, 104.4, 102.9],
    )

    result = simulate(
        path,
        _genome(profit_lock_arm=3.0, profit_lock_giveback=1.0),
    )

    assert result.exit_reason == "profit_lock"
    assert result.pnl_eur == Decimal("2.50")
    assert result.max_favourable_eur == Decimal("4.00")


def test_partial_profit_keeps_runner_until_second_target():
    path = _path(
        [100.2, 100.7, 101.2, 102.2],
        [100.4, 100.9, 101.4, 102.4],
        legs=(_leg(open_price=100.2, volume=0.02),),
    )
    genome = _genome(
        volume_weights=(0.02,),
        target_mode="partial_runner",
        target_value=1.0,
        partial_fraction=0.5,
        runner_target=2.5,
    )

    result = simulate(path, genome)

    assert [item.reason for item in result.exits] == ["partial_target", "runner_target"]
    assert [item.volume for item in result.exits] == [0.01, 0.01]
    assert result.pnl_eur == Decimal("2.50")


def test_time_exit_uses_elapsed_market_time():
    path = _path(
        [100.2, 100.5, 100.7],
        [100.4, 100.7, 100.9],
        interval_seconds=60,
    )

    result = simulate(path, _genome(time_exit_min=2))

    assert result.exit_reason == "time_exit"
    assert result.last_tick_index == 2
    assert result.pnl_eur == Decimal("0.50")


def test_explicit_provider_close_is_causal():
    close = ProviderEvent(BASE + timedelta(seconds=1), "CLOSE_ALL", {})
    path = _path(
        [100.2, 100.7, 102.0],
        [100.4, 100.9, 102.2],
        provider_events=(close,),
    )

    result = simulate(
        path,
        _genome(provider_management_mode="exact"),
    )

    assert result.exit_reason == "provider_close"
    assert result.last_tick_index == 1
    assert result.pnl_eur == Decimal("0.50")


def test_execution_latency_delays_provider_close_instruction():
    close = ProviderEvent(BASE + timedelta(seconds=1), "CLOSE_ALL", {})
    path = _path(
        [100.2, 100.7, 101.2, 101.7],
        [100.4, 100.9, 101.4, 101.9],
        provider_events=(close,),
    )
    genome = _genome(provider_management_mode="exact")

    immediate = simulate(path, genome)
    delayed = simulate(
        path,
        genome,
        execution=ExecutionAssumptions(latency_ms=2_000),
    )

    assert immediate.last_tick_index == 1
    assert immediate.pnl_eur == Decimal("0.50")
    assert delayed.last_tick_index == 3
    assert delayed.pnl_eur == Decimal("1.50")


def test_execution_latency_delays_provider_tp_visibility():
    path = _path(
        [100.2, 101.2, 100.7, 100.8, 101.1],
        [100.4, 101.4, 100.9, 101.0, 101.3],
        legs=(
            _leg(
                open_price=100.2,
                tp=101.0,
                level_at=BASE + timedelta(seconds=1),
            ),
        ),
    )
    genome = _genome(target_mode="provider_per_leg")

    immediate = simulate(path, genome)
    delayed = simulate(
        path,
        genome,
        execution=ExecutionAssumptions(latency_ms=2_000),
    )

    assert immediate.last_tick_index == 1
    assert delayed.last_tick_index == 4
    assert delayed.pnl_eur == Decimal("0.90")


def test_execution_latency_delays_provider_sl_visibility():
    path = _path(
        [100.2, 99.0, 100.0, 100.1, 98.9],
        [100.4, 99.2, 100.2, 100.3, 99.1],
        legs=(
            _leg(
                open_price=100.2,
                sl=99.5,
                level_at=BASE + timedelta(seconds=1),
            ),
        ),
    )
    genome = _genome(stop_mode="provider")

    immediate = simulate(path, genome)
    delayed = simulate(
        path,
        genome,
        execution=ExecutionAssumptions(latency_ms=2_000),
    )

    assert immediate.last_tick_index == 1
    assert delayed.last_tick_index == 4
    assert delayed.pnl_eur == Decimal("-1.30")


def test_adverse_exit_slippage_is_applied_before_money():
    path = _path([100.2, 100.7], [100.4, 100.9])

    result = simulate(
        path,
        _genome(target_mode="fixed_basket", target_value=0.4),
        execution=ExecutionAssumptions(exit_slippage=0.1),
    )

    assert result.exits[0].exit_price == 100.6
    assert result.pnl_eur == Decimal("0.40")


def test_engine_never_reads_ticks_after_decision_exit():
    path = _path(
        [100.2, 101.2, np.nan],
        [100.4, 101.4, np.nan],
    )

    result = simulate(
        path,
        _genome(target_mode="fixed_basket", target_value=1.0),
    )

    assert result.blockers == ()
    assert result.last_tick_index == 1


def test_unfilled_causal_entry_remains_visible_as_zero_exposure():
    path = _path(
        [100.0, 100.1, 100.2],
        [100.2, 100.3, 100.4],
        interval_seconds=60,
    )
    genome = _genome(
        entry_mode="momentum",
        entry_value=2.0,
        entry_expiry_min=2,
    )

    result = simulate(path, genome)

    assert result.unfilled is True
    assert result.pnl_eur == Decimal("0.00")
    assert result.exits == ()
    assert result.blockers == ()


def test_context_filter_can_reject_an_observed_mt5_entry_causally():
    path = _path(
        [100.0, 101.0],
        [100.2, 101.2],
    )
    genome = _genome(
        entry_mode="actual_mt5",
        context_filter_mode="max_spread",
        context_filter_value=0.10,
    )

    result = simulate(path, genome)

    assert result.unfilled is True
    assert result.entries == ()
    assert result.pnl_eur == Decimal("0.00")


def test_exposure_above_observed_baseline_is_really_simulated():
    path = _path([100.2, 101.2], [100.4, 101.4])

    result = simulate(
        path,
        _genome(
            volume_weights=(0.10,),
            target_mode="fixed_basket",
            target_value=10.0,
        ),
    )

    assert result.filled_volume == 0.10
    assert result.pnl_eur == Decimal("10.00")
    assert result.confidence_layer == "counterfactual_entry"


def test_provider_be_instruction_uses_each_simulated_entry_price():
    move_be = ProviderEvent(
        BASE + timedelta(seconds=1),
        "MOVE_SL_TO_BE",
        {"raw_text": "Move SL to BE"},
    )
    path = _path(
        [100.2, 101.5, 100.2],
        [100.4, 101.7, 100.4],
        provider_events=(move_be,),
    )

    result = simulate(
        path,
        _genome(
            be_mode="provider",
            stop_mode="provider",
            provider_management_mode="exact",
        ),
    )

    assert result.exit_reason == "break_even"
    assert result.pnl_eur == Decimal("0.00")


def test_provider_move_sl_to_price_uses_announced_level():
    move_sl = ProviderEvent(
        BASE + timedelta(seconds=1),
        "MOVE_SL_TO_PRICE",
        {"raw_text": "Protect it now, move SL to 100.80"},
    )
    path = _path(
        [100.2, 101.5, 100.8],
        [100.4, 101.7, 101.0],
        provider_events=(move_sl,),
    )

    result = simulate(
        path,
        _genome(
            be_mode="provider",
            stop_mode="provider",
            provider_management_mode="exact",
        ),
    )

    assert result.exit_reason == "provider_sl_move"
    assert result.pnl_eur == Decimal("0.60")


def test_momentum_entry_uses_future_executable_ask_without_hindsight():
    path = _path(
        [100.0, 100.3, 101.1, 102.1],
        [100.2, 100.5, 101.3, 102.3],
    )
    result = simulate(
        path,
        _genome(
            entry_mode="momentum",
            entry_value=1.0,
            entry_expiry_min=1,
            target_mode="fixed_basket",
            target_value=0.8,
        ),
    )

    assert result.entries[0].tick_index == 2
    assert result.entries[0].entry_price == 101.3
    assert result.pnl_eur == Decimal("0.80")


def test_stale_conversion_at_exit_blocks_money_claim():
    path = _path(
        [100.2, 101.2],
        [100.4, 101.4],
        legs=(_leg(open_price=100.2, tp=101.2),),
        fx_valid=(True, False),
        conversion_orientation="account_base_profit_quote",
    )

    result = simulate(
        path,
        _genome(target_mode="provider_per_leg"),
    )

    assert result.pnl_eur is None
    assert "stale_conversion_at_exit:1" in result.blockers
