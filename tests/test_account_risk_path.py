from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np

from research.account_risk_path import compare_account_paths
from research.causal_comparison import SequenceEvent
from research.causal_replay import time_ns
from research.risk_trajectory import RiskQuote, RiskSpec, reconstruct_risk


def _inputs(prices, *, seconds=None, fx_seconds=None, fx_max_age_ms=5000):
    start = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
    seconds = seconds or list(range(len(prices)))
    moments = [start + timedelta(seconds=value) for value in seconds]
    market = (np.asarray([time_ns(value) for value in moments], dtype=np.int64),
              np.asarray(prices, dtype=float), np.asarray(prices, dtype=float))
    fx_seconds = fx_seconds if fx_seconds is not None else seconds
    conversion = (np.asarray([time_ns(start + timedelta(seconds=value)) for value in fx_seconds],
                             dtype=np.int64), np.ones(len(fx_seconds)), np.ones(len(fx_seconds)))
    spec = RiskSpec("EUR", 2, Decimal(100), "account_base_profit_quote", fx_max_age_ms, 5000)
    return moments, market, conversion, spec


def _trade(slot, direction, opened, closed, entry_price, exit_price, volume, money):
    return [SequenceEvent(slot, "entry", opened, direction, entry_price, volume, 0, "market"),
            SequenceEvent(slot, "exit", closed, direction, exit_price, volume, money, "tp")]


def test_account_path_catches_overlap_drawdown_when_net_is_zero():
    moments, market, conversion, spec = _inputs([100, 101, 99, 102])
    observed = {
        "canal2_1": _trade(1, "BUY", moments[0], moments[3], 100, 102, "0.01", "2.00"),
        "canal2_2": _trade(1, "BUY", moments[1], moments[2], 101, 99, "0.01", "-2.00"),
    }
    result = compare_account_paths(observed, observed, market, conversion, spec=spec)
    assert result["status"] == "exact_sampled_path_only"
    assert result["observed"]["booked_net_eur"] == "0.00"
    assert result["observed"]["max_drawdown_eur"] == "4.00"
    assert result["observed"]["max_gross_volume"] == 0.02
    assert result["common_marks"] == 4


def test_account_path_detects_price_difference_with_same_exposure():
    moments, market, conversion, spec = _inputs([100, 99, 101])
    observed = {"canal2_1": _trade(1, "BUY", moments[0], moments[2], 100, 101,
                                    "0.01", "1.00")}
    replay = {"canal2_1": _trade(1, "BUY", moments[0], moments[2], "100.50", 101,
                                  "0.01", "0.50")}
    result = compare_account_paths(observed, replay, market, conversion, spec=spec)
    assert result["status"] == "mismatch"
    assert result["exposure_difference_marks"] == 0
    assert result["money_difference_marks"] > 0
    assert result["max_abs_equity_difference_eur"] == "0.50"


def test_account_path_marks_between_tick_event_with_prior_quote():
    moments, market, conversion, spec = _inputs([100, 99], seconds=[0, 2], fx_seconds=[0, 1, 2])
    middle = moments[0] + timedelta(seconds=1)
    observed = {"canal2_1": _trade(1, "BUY", middle, moments[1], 100, 99,
                                    "0.01", "-1.00")}
    result = compare_account_paths(observed, observed, market, conversion, spec=spec)
    assert result["status"] == "exact_sampled_path_only"
    assert result["common_marks"] == 2
    assert result["observed"]["max_drawdown_eur"] == "1.00"


def test_account_path_blocks_stale_conversion_without_zeroing_money():
    moments, market, conversion, _ = _inputs([100, 99, 101], fx_seconds=[0])
    spec = RiskSpec("EUR", 2, Decimal(100), "account_base_profit_quote", 500, 5000)
    observed = {"canal2_1": _trade(1, "BUY", moments[0], moments[2], 100, 101,
                                    "0.01", "1.00")}
    result = compare_account_paths(observed, observed, market, conversion, spec=spec)
    assert result["status"] == "blocked"
    assert result["unknown_money_marks"] > 0
    assert result["observed"]["max_drawdown_eur"] is None
    assert result["observed"]["booked_net_eur"] == "1.00"


def test_account_path_retrospective_fx_uses_prior_rate_only():
    moments, market, _, _ = _inputs([100, 99, 101])
    conversion = (np.asarray([time_ns(moments[0]), time_ns(moments[0] + timedelta(seconds=3))],
                             dtype=np.int64),
                  np.asarray([1.0, 2.0]), np.asarray([1.0, 2.0]))
    spec = RiskSpec("EUR", 2, Decimal(100), "account_base_profit_quote", 500, 5000)
    observed = {"canal2_1": _trade(1, "BUY", moments[0], moments[2], 100, 101,
                                    "0.01", "1.00")}
    strict = compare_account_paths(observed, observed, market, conversion, spec=spec)
    assert strict["status"] == "blocked"
    bracketed = compare_account_paths(observed, observed, market, conversion, spec=spec,
                                      retrospective_fx_interval_ms=3000)
    assert bracketed["status"] == "exact_sampled_path_only"
    assert bracketed["fx_coverage_mode"] == "retrospective_bracketed"
    assert bracketed["retrospective_fx_bracketed_open_marks"] == 1
    assert bracketed["observed"]["max_drawdown_eur"] == "1.00"


def test_account_path_retains_known_booked_divergence_when_fx_blocks_floating():
    moments, market, conversion, spec = _inputs(
        [100, 101, 102, 100], fx_seconds=[0, 4], fx_max_age_ms=500)
    entry = SequenceEvent(1, "entry", moments[0], "BUY", 100, "0.02", 0, "market")
    observed = {"canal2_1": [
        entry,
        SequenceEvent(1, "exit", moments[1], "BUY", 101, "0.01", "1.00", "expert"),
        SequenceEvent(1, "exit", moments[3], "BUY", 100, "0.01", "-1.00", "expert"),
    ]}
    simulated = {"canal2_1": [
        entry,
        SequenceEvent(1, "exit", moments[1], "BUY", 101, "0.01", "0.00", "expert"),
        SequenceEvent(1, "exit", moments[3], "BUY", 100, "0.01", "0.00", "expert"),
    ]}
    result = compare_account_paths(observed, simulated, market, conversion, spec=spec)
    assert result["status"] == "blocked"
    assert result["unknown_money_marks"] > 0
    assert result["exposure_difference_marks"] == 0
    assert result["observed"]["booked_net_eur"] == "0.00"
    assert result["simulated"]["booked_net_eur"] == "0.00"
    assert result["booked_difference_marks"] >= 2
    assert result["money_difference_marks"] >= 2
    assert result["first_divergence"]["at_ns"] == time_ns(moments[1])
    assert result["first_divergence"]["observed_booked_eur"] == "1.00"
    assert result["first_divergence"]["simulated_booked_eur"] == "0.00"


def test_account_path_supports_partial_closes_and_entry_cost():
    moments, market, conversion, spec = _inputs([100, 101, 102])
    observed = {"canal1_1": [
        SequenceEvent(1, "entry", moments[0], "BUY", 100, "0.02", "-0.10", "market"),
        SequenceEvent(1, "exit", moments[1], "BUY", 101, "0.01", "1.00", "expert"),
        SequenceEvent(1, "exit", moments[2], "BUY", 102, "0.01", "2.00", "expert"),
    ]}
    result = compare_account_paths(observed, observed, market, conversion, spec=spec)
    assert result["status"] == "exact_sampled_path_only"
    assert result["observed"]["booked_net_eur"] == "2.90"
    assert result["observed"]["max_gross_volume"] == 0.02


def test_account_path_retains_all_zero_fill_cohort():
    _, market, conversion, spec = _inputs([100, 101])
    cohort = {"canal1_1": [], "canal2_2": []}
    result = compare_account_paths(cohort, cohort, market, conversion, spec=spec)
    assert result["status"] == "exact_sampled_path_only"
    assert result["observed"]["booked_net_eur"] == "0.00"
    assert result["observed"]["max_drawdown_eur"] == "0.00"
    assert result["common_marks"] == 0


def test_account_path_matches_independent_decimal_risk_reconstruction():
    moments, _, _, spec = _inputs([100, 99, 102, 100])
    bid = [100.00, 99.00, 102.00, 100.00]
    ask = [100.02, 99.02, 102.02, 100.02]
    fx_bid = [1.10000, 1.10001, 1.10002, 1.10003]
    fx_ask = [1.10010, 1.10011, 1.10012, 1.10013]
    market = (np.asarray([time_ns(at) for at in moments], dtype=np.int64),
              np.asarray(bid), np.asarray(ask))
    conversion = (market[0], np.asarray(fx_bid), np.asarray(fx_ask))
    observed = {
        "canal1_1": [
            SequenceEvent(1, "entry", moments[0], "BUY", "100.02", "0.02", 0, "market"),
            SequenceEvent(1, "exit", moments[2], "BUY", "102.00", "0.01", "1.80", "expert"),
            SequenceEvent(1, "exit", moments[3], "BUY", "100.00", "0.01", "-0.02", "expert"),
        ],
        "canal2_2": _trade(2, "SELL", moments[1], moments[3], "99.00", "100.02",
                            "0.01", "-0.93"),
    }
    quotes = [RiskQuote(at, bid[index], ask[index], at, fx_bid[index], fx_ask[index])
              for index, at in enumerate(moments)]
    reference = reconstruct_risk(
        [event for rows in observed.values() for event in rows], quotes, spec=spec)
    account = compare_account_paths(observed, observed, market, conversion, spec=spec)
    assert reference["blockers"] == []
    assert account["status"] == "exact_sampled_path_only"
    assert account["observed"]["booked_net_eur"] == str(reference["metrics"]["final_net"])
    assert account["observed"]["max_drawdown_eur"] == str(reference["metrics"]["max_drawdown"])
    assert account["observed"]["max_gross_volume"] == float(reference["metrics"]["max_gross_volume"])
