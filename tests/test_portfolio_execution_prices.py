from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

from research.dubai_iterative.engine import (
    EntryRecord,
    ExecutionAssumptions,
    ExitRecord,
    SimulationResult,
)
from research.dubai_iterative.portfolio import reconstruct_portfolio
from research.dubai_iterative.protection_contract import (
    ProtectionEvent,
    ProtectionProfile,
)


def _case(
    direction: str,
    *,
    exit_quote: float,
    exit_price: float,
    reason: str,
    entry_price: float,
    pnl: str,
    protection_events=(),
):
    start = datetime(2026, 9, 9, 5, 0, tzinfo=timezone.utc)
    moments = (start, start + timedelta(seconds=1))
    bid = np.asarray((entry_price, exit_quote), dtype=float)
    ask = bid + 0.2
    if direction == "SELL":
        ask[-1] = exit_quote
        bid[-1] = exit_quote - 0.2
    path = SimpleNamespace(
        signal_id="signal",
        direction=direction,
        times_ns=np.asarray(
            [int(moment.timestamp() * 1_000_000_000) for moment in moments],
            dtype=np.int64,
        ),
        bid=bid,
        ask=ask,
        fx_bid=np.ones(2, dtype=float),
        fx_ask=np.ones(2, dtype=float),
        fx_valid=np.ones(2, dtype=bool),
        contract_size=100.0,
        conversion_orientation="identity",
        currency_digits=2,
    )
    entry = EntryRecord("ticket", 0, moments[0], entry_price, 0.01, "test")
    exit_record = ExitRecord(
        "ticket",
        1,
        moments[1],
        entry_price,
        exit_price,
        0.01,
        Decimal(pnl),
        reason,
    )
    result = SimulationResult(
        signal_id="signal",
        strategy_fingerprint="strategy",
        confidence_layer="counterfactual_entry",
        entries=(entry,),
        exits=(exit_record,),
        pnl_eur=Decimal(pnl),
        exit_reason=reason,
        max_favourable_eur=Decimal("0.00"),
        max_adverse_eur=Decimal("0.00"),
        max_floating_drawdown_eur=Decimal("0.00"),
        max_favourable_move=0.0,
        max_adverse_move=0.0,
        blockers=(),
        last_tick_index=1,
        unfilled=False,
        filled_volume=0.01,
        protection_events=tuple(protection_events),
    )
    return path, result, moments


@pytest.mark.parametrize(
    ("direction", "kind", "exit_quote", "exit_price", "reason", "entry_price", "pnl"),
    (
        ("BUY", "market", 103.0, 102.9, "time_exit", 100.0, "2.90"),
        ("SELL", "market", 100.0, 100.1, "time_exit", 103.0, "2.90"),
        ("BUY", "stop", 98.0, 97.9, "fixed_sl", 100.0, "-2.10"),
        ("SELL", "stop", 105.0, 105.1, "fixed_sl", 103.0, "-2.10"),
        ("BUY", "tp", 103.0, 101.9, "per_leg_target", 100.0, "1.90"),
        ("SELL", "tp", 100.0, 101.1, "provider_tp", 103.0, "1.90"),
    ),
)
def test_portfolio_accepts_only_the_observable_execution_convention(
    direction,
    kind,
    exit_quote,
    exit_price,
    reason,
    entry_price,
    pnl,
):
    path, result, _ = _case(
        direction,
        exit_quote=exit_quote,
        exit_price=exit_price,
        reason=reason,
        entry_price=entry_price,
        pnl=pnl,
    )

    report = reconstruct_portfolio(
        (path,),
        (result,),
        execution=ExecutionAssumptions(exit_slippage=0.1),
    )

    assert report.blockers == (), kind
    assert report.net_eur == Decimal(pnl)


@pytest.mark.parametrize(
    ("direction", "reason", "exit_quote", "tampered_price", "entry_price", "pnl"),
    (
        ("BUY", "per_leg_target", 103.0, 103.5, 100.0, "3.50"),
        ("SELL", "provider_tp", 100.0, 99.5, 103.0, "3.50"),
        ("BUY", "time_exit", 103.0, 101.9, 100.0, "1.90"),
        ("SELL", "fixed_sl", 105.0, 106.0, 103.0, "-3.00"),
    ),
)
def test_portfolio_rejects_optimistic_targets_and_non_target_price_substitution(
    direction,
    reason,
    exit_quote,
    tampered_price,
    entry_price,
    pnl,
):
    path, result, _ = _case(
        direction,
        exit_quote=exit_quote,
        exit_price=tampered_price,
        reason=reason,
        entry_price=entry_price,
        pnl=pnl,
    )

    report = reconstruct_portfolio(
        (path,),
        (result,),
        execution=ExecutionAssumptions(exit_slippage=0.1),
    )

    assert report.blockers == ("execution_scenario_mismatch:signal:ticket",)


def _profile():
    return ProtectionProfile(
        point=0.01,
        digits=2,
        stops_level_points=20,
        freeze_level_points=0,
        processing_delay_ms=0,
        acknowledgement_delay_ms=0,
        retry_delay_ms=0,
    )


def test_profile_target_requires_matching_closed_protection_trace():
    path, result, moments = _case(
        "BUY",
        exit_quote=103.0,
        exit_price=101.9,
        reason="per_leg_target",
        entry_price=100.0,
        pnl="1.90",
    )
    closed = ProtectionEvent(
        "ticket",
        1,
        int(moments[1].timestamp() * 1_000_000_000),
        0,
        "closed",
        90.0,
        102.0,
        "per_leg_target",
    )
    valid = replace(result, protection_events=(closed,))
    tampered = replace(valid, protection_events=(replace(closed, tp=102.5),))
    execution = ExecutionAssumptions(exit_slippage=0.1, protection=_profile())

    accepted = reconstruct_portfolio((path,), (valid,), execution=execution)
    missing = reconstruct_portfolio((path,), (result,), execution=execution)
    rejected = reconstruct_portfolio((path,), (tampered,), execution=execution)

    assert accepted.blockers == ()
    assert missing.blockers == ("execution_scenario_mismatch:signal:ticket",)
    assert rejected.blockers == ("execution_scenario_mismatch:signal:ticket",)


def test_exit_entry_price_must_match_entry_before_money_is_checked():
    path, result, _ = _case(
        "BUY",
        exit_quote=102.0,
        exit_price=102.0,
        reason="time_exit",
        entry_price=100.0,
        pnl="2.00",
    )
    mismatched_exit = replace(
        result.exits[0],
        entry_price=99.0,
        pnl_eur=Decimal("3.00"),
    )
    tampered = replace(result, exits=(mismatched_exit,), pnl_eur=Decimal("3.00"))

    report = reconstruct_portfolio((path,), (tampered,))

    assert report.blockers == ("exit_entry_price_mismatch:signal:ticket",)


def test_limit_exit_money_is_still_recomputed_from_the_bound_entry():
    path, result, _ = _case(
        "BUY",
        exit_quote=103.0,
        exit_price=102.0,
        reason="per_leg_target",
        entry_price=100.0,
        pnl="3.00",
    )

    report = reconstruct_portfolio((path,), (result,))

    assert report.blockers == ("exit_money_mismatch:signal:ticket",)
