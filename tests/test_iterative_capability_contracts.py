from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

import numpy as np
import pytest

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import DubaiLeg, DubaiPath, LevelEvent, ProviderEvent
from research.dubai_iterative.engine import SimulationResult, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import OracleResult, oracle_simulate


BASE = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)


def _frozen(values, dtype=float):
    result = np.asarray(values, dtype=dtype)
    result.setflags(write=False)
    return result


def _leg(
    ticket: str,
    *,
    direction: str,
    opened_minute: int = 0,
    open_price: float | None = None,
    role: str = "market_a",
    tp_events: tuple[LevelEvent, ...] = (),
    sl_events: tuple[LevelEvent, ...] = (),
) -> DubaiLeg:
    if open_price is None:
        open_price = 100.2 if direction == "BUY" else 100.0
    return DubaiLeg(
        ticket=ticket,
        role=role,
        volume=0.01,
        opened_at=BASE + timedelta(minutes=opened_minute),
        open_price=open_price,
        closed_at=None,
        close_price=None,
        close_reason=None,
        actual_pnl_eur=Decimal("0.00"),
        tp_events=tp_events,
        sl_events=sl_events,
    )


def _path(
    bid: list[float],
    ask: list[float],
    *,
    direction: str = "BUY",
    legs: tuple[DubaiLeg, ...] | None = None,
    provider_events: tuple[ProviderEvent, ...] = (),
    interval_seconds: int = 60,
) -> DubaiPath:
    bid_values = _frozen(bid)
    ask_values = _frozen(ask)
    legs = legs or (_leg("observed-1", direction=direction),)
    count = len(bid)
    return DubaiPath(
        signal_id=f"capability-{direction.lower()}",
        day="2026-09-09",
        direction=direction,
        signal_observed_at=BASE,
        opened_at=min(leg.opened_at for leg in legs),
        actual_pnl_eur=Decimal("0.00"),
        legs=legs,
        provider_events=provider_events,
        times_ns=_frozen(
            [
                int((BASE + timedelta(seconds=index * interval_seconds)).timestamp())
                * 1_000_000_000
                for index in range(count)
            ],
            dtype=np.int64,
        ),
        bid=bid_values,
        ask=ask_values,
        exit_quotes=bid_values if direction == "BUY" else ask_values,
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


def _genome(**changes) -> StrategyGenome:
    values = {
        "schema_version": 2,
        "entry_mode": "signal_market",
        "entry_value": None,
        "entry_confirmation_value": None,
        "entry_expiry_min": 10,
        "entry_ladder_mode": "simultaneous",
        "entry_ladder_step": None,
        "leg_count": 1,
        "volume_weights": (0.01,),
        "target_mode": "none",
        "target_value": None,
        "partial_fraction": 0.0,
        "runner_target": None,
        "target_steps": (),
        "be_mode": "none",
        "be_trigger": None,
        "stop_mode": "none",
        "stop_value": None,
        "profit_lock_arm": None,
        "profit_lock_giveback": None,
        "time_exit_min": 10,
        "time_exit_mode": "none",
        "provider_management_mode": "ignore",
        "context_filter_mode": "none",
        "context_filter_value": None,
        "pending_entry_policy": "none",
        "trailing_distance": None,
        "hard_stop_eur_per_leg": None,
    }
    values.update(changes)
    return StrategyGenome.baseline().with_change(**values)


@dataclass(frozen=True)
class Expected:
    entries: tuple[tuple[int, float, float, str], ...]
    exits: tuple[tuple[int, float, float, Decimal, str], ...]
    exit_reason: str
    pnl_eur: Decimal
    filled_volume: float
    unfilled: bool = False
    blockers: tuple[str, ...] = ()
    last_tick_index: int | None = None


@dataclass(frozen=True)
class Scenario:
    name: str
    path: DubaiPath
    genome: StrategyGenome
    expected: Expected


def _entry(index: int, price: float, source: str = "causal_signal_market"):
    return ((index, price, 0.01, source),)


def _exit(index: int, price: float, pnl: str, reason: str):
    return ((index, price, 0.01, Decimal(pnl), reason),)


def _unfilled() -> Expected:
    return Expected(
        entries=(),
        exits=(),
        exit_reason="not_filled",
        pnl_eur=Decimal("0.00"),
        filled_volume=0.0,
        unfilled=True,
        last_tick_index=-1,
    )


def _scenarios() -> tuple[Scenario, ...]:
    delayed_buy_legs = (
        _leg("buy-early", direction="BUY", open_price=100.2),
        _leg(
            "buy-late",
            direction="BUY",
            opened_minute=2,
            open_price=101.2,
            role="scale_out_leg",
        ),
    )
    delayed_sell_legs = (
        _leg("sell-early", direction="SELL", open_price=100.0),
        _leg(
            "sell-late",
            direction="SELL",
            opened_minute=2,
            open_price=99.0,
            role="scale_out_leg",
        ),
    )
    historical_tp = LevelEvent(
        BASE - timedelta(minutes=1), 102.2, "confirmed", "provider"
    )
    future_tp = LevelEvent(
        BASE + timedelta(minutes=1), 110.0, "confirmed", "provider"
    )
    historical_sl = LevelEvent(
        BASE - timedelta(minutes=1), 99.2, "confirmed", "provider"
    )
    future_sl = LevelEvent(
        BASE + timedelta(minutes=1), 100.0, "confirmed", "provider"
    )

    return (
        Scenario(
            "pullback_buy_exact_cross",
            _path([100.0, 99.8, 99.0, 100.2], [100.2, 100.0, 99.2, 100.4]),
            _genome(
                entry_mode="pullback",
                entry_value=1.0,
                target_mode="fixed_basket",
                target_value=1.0,
            ),
            Expected(
                _entry(2, 99.2, "causal_pullback"),
                _exit(3, 100.2, "1.00", "basket_target"),
                "basket_target",
                Decimal("1.00"),
                0.01,
                last_tick_index=3,
            ),
        ),
        Scenario(
            "pullback_sell_exact_cross",
            _path(
                [100.0, 100.2, 101.0, 99.8],
                [100.2, 100.4, 101.2, 100.0],
                direction="SELL",
            ),
            _genome(
                entry_mode="pullback",
                entry_value=1.0,
                target_mode="fixed_basket",
                target_value=1.0,
            ),
            Expected(
                _entry(2, 101.0, "causal_pullback"),
                _exit(3, 100.0, "1.00", "basket_target"),
                "basket_target",
                Decimal("1.00"),
                0.01,
                last_tick_index=3,
            ),
        ),
        Scenario(
            "momentum_sell_exact_cross",
            _path(
                [100.0, 99.6, 99.0, 97.8],
                [100.2, 99.8, 99.2, 98.0],
                direction="SELL",
            ),
            _genome(
                entry_mode="momentum",
                entry_value=1.0,
                target_mode="fixed_basket",
                target_value=1.0,
            ),
            Expected(
                _entry(2, 99.0, "causal_momentum"),
                _exit(3, 98.0, "1.00", "basket_target"),
                "basket_target",
                Decimal("1.00"),
                0.01,
                last_tick_index=3,
            ),
        ),
        Scenario(
            "no_entry_control",
            _path([100.0, 105.0], [100.2, 105.2]),
            _genome(entry_mode="no_entry"),
            _unfilled(),
        ),
        Scenario(
            "basket_money_buy_exact_threshold",
            _path([100.0, 99.2], [100.2, 99.4]),
            _genome(stop_mode="basket_money", stop_value=1.0),
            Expected(
                _entry(0, 100.2),
                _exit(1, 99.2, "-1.00", "basket_stop"),
                "basket_stop",
                Decimal("-1.00"),
                0.01,
                last_tick_index=1,
            ),
        ),
        Scenario(
            "basket_money_sell_wins_over_provider_close",
            _path(
                [100.0, 100.8],
                [100.2, 101.0],
                direction="SELL",
                provider_events=(
                    ProviderEvent(BASE + timedelta(minutes=1), "CLOSE_ALL", {}),
                ),
            ),
            _genome(
                stop_mode="basket_money",
                stop_value=1.0,
                provider_management_mode="exact",
            ),
            Expected(
                _entry(0, 100.0),
                _exit(1, 101.0, "-1.00", "basket_stop"),
                "basket_stop",
                Decimal("-1.00"),
                0.01,
                last_tick_index=1,
            ),
        ),
        Scenario(
            "fixed_move_buy_fills_at_gap_quote",
            _path([100.0, 98.7], [100.2, 98.9]),
            _genome(stop_mode="fixed_move", stop_value=1.0),
            Expected(
                _entry(0, 100.2),
                _exit(1, 98.7, "-1.50", "fixed_sl"),
                "fixed_sl",
                Decimal("-1.50"),
                0.01,
                last_tick_index=1,
            ),
        ),
        Scenario(
            "fixed_move_sell_fills_at_gap_quote",
            _path([100.0, 101.3], [100.2, 101.5], direction="SELL"),
            _genome(stop_mode="fixed_move", stop_value=1.0),
            Expected(
                _entry(0, 100.0),
                _exit(1, 101.5, "-1.50", "fixed_sl"),
                "fixed_sl",
                Decimal("-1.50"),
                0.01,
                last_tick_index=1,
            ),
        ),
        Scenario(
            "delayed_be_buy_uses_each_leg_open_time",
            _path(
                [100.0, 101.0, 101.0, 100.2, 101.2],
                [100.2, 101.2, 101.2, 100.4, 101.4],
                legs=delayed_buy_legs,
            ),
            _genome(
                entry_mode="actual_mt5",
                leg_count=2,
                volume_weights=(0.01, 0.01),
                be_mode="delayed",
                be_trigger=2.0,
            ),
            Expected(
                entries=(
                    (0, 100.2, 0.01, "observed_mt5_fill"),
                    (2, 101.2, 0.01, "observed_mt5_fill"),
                ),
                exits=(
                    (3, 100.2, 0.01, Decimal("0.00"), "break_even"),
                    (4, 101.2, 0.01, Decimal("0.00"), "break_even"),
                ),
                exit_reason="break_even",
                pnl_eur=Decimal("0.00"),
                filled_volume=0.02,
                last_tick_index=4,
            ),
        ),
        Scenario(
            "delayed_be_sell_uses_each_leg_open_time",
            _path(
                [100.0, 99.0, 99.0, 99.8, 98.8],
                [100.2, 99.2, 99.2, 100.0, 99.0],
                direction="SELL",
                legs=delayed_sell_legs,
            ),
            _genome(
                entry_mode="actual_mt5",
                leg_count=2,
                volume_weights=(0.01, 0.01),
                be_mode="delayed",
                be_trigger=2.0,
            ),
            Expected(
                entries=(
                    (0, 100.0, 0.01, "observed_mt5_fill"),
                    (2, 99.0, 0.01, "observed_mt5_fill"),
                ),
                exits=(
                    (3, 100.0, 0.01, Decimal("0.00"), "break_even"),
                    (4, 99.0, 0.01, Decimal("0.00"), "break_even"),
                ),
                exit_reason="break_even",
                pnl_eur=Decimal("0.00"),
                filled_volume=0.02,
                last_tick_index=4,
            ),
        ),
        Scenario(
            "profit_only_closes_positive_at_horizon",
            _path([100.0, 100.5, 101.2], [100.2, 100.7, 101.4]),
            _genome(time_exit_min=2, time_exit_mode="profit_only"),
            Expected(
                _entry(0, 100.2),
                _exit(2, 101.2, "1.00", "time_exit"),
                "time_exit",
                Decimal("1.00"),
                0.01,
                last_tick_index=2,
            ),
        ),
        Scenario(
            "profit_only_does_not_close_zero_at_horizon",
            _path(
                [100.0, 100.0, 99.8],
                [100.2, 100.1, 100.0],
                direction="SELL",
            ),
            _genome(time_exit_min=2, time_exit_mode="profit_only"),
            Expected(
                _entry(0, 100.0),
                _exit(2, 100.0, "0.00", "data_end"),
                "data_end",
                Decimal("0.00"),
                0.01,
                blockers=("path_ended_before_strategy_exit",),
                last_tick_index=2,
            ),
        ),
        Scenario(
            "profit_only_does_not_close_negative_at_horizon",
            _path([100.0, 99.5, 99.2], [100.2, 99.7, 99.4]),
            _genome(time_exit_min=2, time_exit_mode="profit_only"),
            Expected(
                _entry(0, 100.2),
                _exit(2, 99.2, "-1.00", "data_end"),
                "data_end",
                Decimal("-1.00"),
                0.01,
                blockers=("path_ended_before_strategy_exit",),
                last_tick_index=2,
            ),
        ),
        Scenario(
            "time_exit_none_stays_open_beyond_profitable_horizon",
            _path(
                [100.0, 99.4, 98.8, 97.8],
                [100.2, 99.6, 99.0, 98.0],
                direction="SELL",
            ),
            _genome(time_exit_min=2, time_exit_mode="none"),
            Expected(
                _entry(0, 100.0),
                _exit(3, 98.0, "2.00", "data_end"),
                "data_end",
                Decimal("2.00"),
                0.01,
                blockers=("path_ended_before_strategy_exit",),
                last_tick_index=3,
            ),
        ),
        Scenario(
            "time_window_rejects_entry_after_utc_cutoff",
            _path([100.0, 101.0], [100.2, 101.2]),
            _genome(context_filter_mode="time_window", context_filter_value=8.5),
            _unfilled(),
        ),
        Scenario(
            "max_volatility_uses_only_pre_entry_lookback",
            _path([100.0, 101.0, 102.0], [100.2, 101.2, 102.2]),
            _genome(
                entry_mode="momentum",
                entry_value=2.0,
                context_filter_mode="max_volatility",
                context_filter_value=1.5,
            ),
            _unfilled(),
        ),
        Scenario(
            "min_reward_risk_does_not_read_future_provider_levels",
            _path(
                [100.0, 100.5],
                [100.2, 100.7],
                legs=(
                    _leg(
                        "provider-history",
                        direction="BUY",
                        tp_events=(historical_tp, future_tp),
                        sl_events=(historical_sl, future_sl),
                    ),
                ),
            ),
            _genome(
                context_filter_mode="min_reward_risk",
                context_filter_value=3.0,
            ),
            _unfilled(),
        ),
    )


SCENARIOS = _scenarios()
FAST = FastEvaluator()
Engine = Callable[[DubaiPath, StrategyGenome], SimulationResult | OracleResult]
ENGINES: tuple[tuple[str, Engine], ...] = (
    ("scalar", simulate),
    ("fast", FAST),
    ("oracle", oracle_simulate),
)


def _assert_hand_calculated(result, expected: Expected) -> None:
    assert tuple(
        (entry.tick_index, entry.entry_price, entry.volume, entry.source)
        for entry in result.entries
    ) == expected.entries
    assert tuple(
        (exit.tick_index, exit.exit_price, exit.volume, exit.pnl_eur, exit.reason)
        for exit in result.exits
    ) == expected.exits
    assert result.exit_reason == expected.exit_reason
    assert result.pnl_eur == expected.pnl_eur
    assert result.filled_volume == expected.filled_volume
    assert result.unfilled is expected.unfilled
    assert result.blockers == expected.blockers
    if expected.last_tick_index is not None:
        assert result.last_tick_index == expected.last_tick_index


@pytest.mark.parametrize("engine_name,engine", ENGINES, ids=lambda value: value if isinstance(value, str) else None)
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_iterative_capability_has_hand_calculated_outcome(
    scenario: Scenario,
    engine_name: str,
    engine: Engine,
) -> None:
    assert scenario.genome.validation_errors() == (), engine_name

    result = engine(scenario.path, scenario.genome)

    _assert_hand_calculated(result, scenario.expected)
