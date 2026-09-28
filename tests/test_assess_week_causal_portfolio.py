from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from research.causal_replay import CausalSignal, time_ns
from research.dubai_iterative.engine import EntryRecord, ExitRecord, SimulationResult
from tools import assess_week_causal_portfolio as portfolio
from tools import run_causal_controls as causal


def _result(signal_id, ticket, entry, exit_index, moments, prices, pnl):
    return SimulationResult(
        signal_id=signal_id, strategy_fingerprint=causal.policies()["canal2"].fingerprint,
        confidence_layer="counterfactual_entry",
        entries=(EntryRecord(ticket, entry, moments[entry], prices[entry], 0.01, "test"),),
        exits=(ExitRecord(ticket, exit_index, moments[exit_index], prices[entry],
                          prices[exit_index], 0.01, Decimal(pnl), "test_exit"),),
        pnl_eur=Decimal(pnl), exit_reason="test_exit",
        max_favourable_eur=Decimal("0"), max_adverse_eur=Decimal("0"),
        max_floating_drawdown_eur=Decimal("0"), max_favourable_move=0.0,
        max_adverse_move=0.0, blockers=(), last_tick_index=exit_index,
        unfilled=False, filled_volume=0.01,
    )


def test_shared_account_risk_includes_overlapping_signals():
    start = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
    moments = [start + timedelta(seconds=index) for index in range(4)]
    prices = [100.0, 101.0, 99.0, 102.0]
    stamps = np.asarray([time_ns(moment) for moment in moments], dtype=np.int64)
    market = (stamps, np.asarray(prices), np.asarray(prices))
    conversion = (stamps, np.ones(4), np.ones(4))
    signals = {f"canal2_{index}": CausalSignal(
        signal_id=f"canal2_{index}", channel="canal2", direction="BUY",
        observed_at=moments[index - 1], published_at=moments[index - 1],
        message_revision_id=f"rev-{index}") for index in (1, 2)}
    first = _result("canal2_1", "a", 0, 3, moments, prices, "2.00")
    second_moments = moments[1:]
    second_prices = prices[1:]
    second = _result("canal2_2", "b", 0, 1, second_moments, second_prices, "-2.00")
    rows = [{"signal_id": signal_id, "status": "diagnostic_only",
             "latency_ms": 0, "entry_fill_latency_ms": 0, "result": asdict(result)}
            for signal_id, result in (("canal2_1", first), ("canal2_2", second))]
    report = portfolio._scenario_report(
        {"contract_size": 100.0, "currency_digits": 2, "fx_max_age_ms": 5000},
        rows, signals, market, conversion, {"XAUUSD": "market", "EURUSD": "fx"})
    assert report["status"] == "diagnostic_only"
    assert report["filled_signal_count"] == 2
    assert report["account"]["net_eur"] == Decimal("0.00")
    assert report["account"]["max_drawdown_eur"] == Decimal("4.00")
    assert report["account"]["max_concurrent_signals"] == 2

    rows[0]["result"]["strategy_fingerprint"] = "wrong-policy"
    with pytest.raises(ValueError, match="result identity"):
        portfolio._scenario_report(
            {"contract_size": 100.0, "currency_digits": 2, "fx_max_age_ms": 5000},
            rows, signals, market, conversion, {"XAUUSD": "market", "EURUSD": "fx"})


def test_shared_account_risk_blocks_incomplete_signal_without_dropping_it():
    rows = [{"signal_id": "canal2_1", "status": "diagnostic_only"},
            {"signal_id": "canal2_2", "status": "censored_data_end"}]
    report = portfolio._scenario_report({}, rows, {}, None, None, {})
    assert report["status"] == "blocked_prior_stage"
    assert report["account"] is None
    assert report["excluded_signals"] == [
        {"signal_id": "canal2_2", "status": "censored_data_end"}]
    assert report["not_assessed_signal_ids"] == ["canal2_1"]


def test_terminal_mark_keeps_drawdown_but_not_realized_outcome():
    start = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
    moments = [start + timedelta(seconds=index) for index in range(3)]
    prices = [100.0, 98.0, 102.0]
    stamps = np.asarray([time_ns(moment) for moment in moments], dtype=np.int64)
    market = (stamps, np.asarray(prices), np.asarray(prices))
    conversion = (stamps, np.ones(3), np.ones(3))
    signal = CausalSignal("canal2_1", "canal2", "BUY", moments[0], moments[0], "rev-1")
    closed = _result("canal2_1", "a", 0, 2, moments, prices, "2.00")
    terminal = replace(closed, exits=(replace(closed.exits[0], reason="data_end"),),
                       exit_reason="data_end", blockers=("path_ended_before_strategy_exit",))
    row = {"signal_id": "canal2_1", "status": "blocked_simulation",
           "censored_data_end": True, "latency_ms": 0,
           "entry_fill_latency_ms": 0, "result": asdict(terminal)}
    report = portfolio._scenario_report(
        {"contract_size": 100.0, "currency_digits": 2, "fx_max_age_ms": 5000},
        [row], {signal.signal_id: signal}, market, conversion,
        {"XAUUSD": "market", "EURUSD": "fx"})
    assert report["status"] == "censored_terminal_mark"
    assert report["censored_terminal_signal_ids"] == ["canal2_1"]
    assert report["hypothetical_exit_complete"] is False
    assert report["account"]["max_drawdown_eur"] == Decimal("2.00")
    assert report["account_net_interpretation"] == "hypothetical_terminal_liquidation_not_realized"
    row["result"]["blockers"] = [*row["result"]["blockers"], "stale_conversion_quote"]
    blocked = portfolio._scenario_report(
        {"contract_size": 100.0, "currency_digits": 2, "fx_max_age_ms": 5000},
        [row], {signal.signal_id: signal}, market, conversion,
        {"XAUUSD": "market", "EURUSD": "fx"})
    assert blocked["status"] == "blocked_prior_stage"
    assert blocked["account"] is None
