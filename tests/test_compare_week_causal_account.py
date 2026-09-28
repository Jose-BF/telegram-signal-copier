from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import numpy as np
import pytest

from research.causal_comparison import SequenceEvent, compare_sequences
from research.causal_replay import time_ns
from research.risk_trajectory import RiskSpec
from tools import compare_week_causal_account as account
from tools import run_week_causal_controls as weekly
from tools.audit_native_money_anchor import digest
from tools.compare_week_causal_account import (compare_scenario, scenario_events,
                                               verify_observed_rows)


def _inputs():
    start = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
    moments = [start + timedelta(seconds=index) for index in range(4)]
    times = np.asarray([time_ns(value) for value in moments], dtype=np.int64)
    market = times, np.asarray([100., 101., 99., 102.]), np.asarray([100., 101., 99., 102.])
    conversion = times, np.ones(4), np.ones(4)
    spec = RiskSpec("EUR", 2, Decimal(100), "account_base_profit_quote", 5000, 5000)
    return moments, market, conversion, spec


def _trade(opened, closed, entry, exit_price, money):
    return [SequenceEvent(1, "entry", opened, "BUY", entry, "0.01", 0, "market"),
            SequenceEvent(1, "exit", closed, "BUY", exit_price, "0.01", money, "tp")]


def _row(signal_id, actual, virtual):
    return {"signal_id": signal_id, "comparison": compare_sequences(actual, virtual)}


def test_week_account_combines_overlapping_signals_and_detects_changed_entry():
    moments, market, conversion, spec = _inputs()
    first = _trade(moments[0], moments[3], 100, 102, "2.00")
    second = _trade(moments[1], moments[2], 101, 99, "-2.00")
    changed = _trade(moments[1], moments[2], "101.50", 99, "-2.50")
    ids = ["canal2_1", "canal2_2", "canal1_3"]
    rows = [_row(ids[0], first, first), _row(ids[1], second, changed),
            _row(ids[2], [], [])]
    result = compare_scenario(ids, rows, market, conversion, spec, 60_000)
    assert result["status"] == "diagnostic_only"
    assert result["included_signal_count"] == 3
    assert result["strict_causal"]["status"] == "mismatch"
    assert result["strict_causal"]["observed"]["booked_net_eur"] == "0.00"
    assert result["strict_causal"]["observed"]["max_drawdown_eur"] == "4.00"
    assert result["strict_causal"]["observed"]["max_gross_volume"] == 0.02
    assert result["strict_causal"]["exposure_difference_marks"] == 0
    assert result["strict_causal"]["money_difference_marks"] > 0


def test_week_account_blocks_entire_cohort_if_one_signal_is_unverified():
    moments, market, conversion, spec = _inputs()
    first = _trade(moments[0], moments[3], 100, 102, "2.00")
    rows = [_row("canal2_1", first, first),
            {"signal_id": "canal2_2", "comparison": {"status": "blocked",
             "blockers": ["native fill status unknown"]}}]
    result = compare_scenario(["canal2_1", "canal2_2"], rows, market,
                              conversion, spec, 60_000)
    assert result["status"] == "blocked_prior_stage"
    assert result["included_signal_count"] == 0
    assert result["excluded_signal_count"] == 1
    assert result["strict_causal"] is None
    assert result["blockers"][0]["signal_id"] == "canal2_2"


def test_week_account_accepts_verified_zero_fill_cohort():
    _, market, conversion, spec = _inputs()
    ids = ["canal1_1", "canal2_2"]
    result = compare_scenario(ids, [_row(signal_id, [], []) for signal_id in ids],
                              market, conversion, spec, 60_000)
    assert result["strict_causal"]["common_marks"] == 0
    assert result["strict_causal"]["observed"]["max_drawdown_eur"] == "0.00"


def test_week_account_rejects_missing_or_duplicate_signal_rows():
    rows = [_row("canal1_1", [], [])]
    with pytest.raises(ValueError, match="denominator"):
        scenario_events(["canal1_1", "canal2_2"], rows)
    with pytest.raises(ValueError, match="denominator"):
        scenario_events(["canal1_1"], rows * 2)


def test_week_account_rechecks_observed_events_against_native_legs():
    moments, _, _, _ = _inputs()
    actual = _trade(moments[0], moments[3], 100, 102, "2.00")
    row = {**_row("canal2_1", actual, actual), "scenario": {"latency_ms": 0},
           "observed_position_count": 1,
           "observed_fill_status": "reconciled_native_fills"}
    assert verify_observed_rows(["canal2_1"], [row], {"canal2_1": actual}) == 1
    forged = _trade(moments[0], moments[3], "100.50", 102, "1.50")
    with pytest.raises(ValueError, match="differ from native"):
        verify_observed_rows(["canal2_1"], [row], {"canal2_1": forged})


def test_week_account_rejects_unproven_or_relabelled_no_fill():
    row = {**_row("canal1_1", [], []), "scenario": {"latency_ms": 0},
           "observed_position_count": 0,
           "observed_fill_status": "verified_no_xau_fill_in_captured_deals"}
    assert verify_observed_rows(["canal1_1"], [row], {"canal1_1": []}) == 1
    with pytest.raises(ValueError, match="unverified"):
        verify_observed_rows(["canal1_1"], [row], {})
    row["observed_fill_status"] = "reconciled_native_fills"
    with pytest.raises(ValueError, match="differ from native"):
        verify_observed_rows(["canal1_1"], [row], {"canal1_1": []})


def test_native_overlap_uses_actual_open_intervals_not_first_to_last_span():
    positions = [
        {"signal_id": "canal2_1", "entry_msc": 0, "exit_msc": 10},
        {"signal_id": "canal2_1", "entry_msc": 20, "exit_msc": 30},
        {"signal_id": "canal2_2", "entry_msc": 10, "exit_msc": 20},
        {"signal_id": "canal2_3", "entry_msc": 25, "exit_msc": 40},
        {"signal_id": "canal1_4", "entry_msc": 25, "exit_msc": 40},
    ]
    result = account.native_overlap(positions)
    assert result["max_concurrent_signals_by_channel"] == {"canal1": 1, "canal2": 2}
    assert result["same_channel_overlap_pairs"] == [
        {"signal_ids": ["canal2_1", "canal2_3"], "overlap_ms": 5}]


def test_week_account_orchestrator_binds_empty_native_cohort(tmp_path, monkeypatch):
    def write(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    start = datetime(2026, 9, 14, tzinfo=timezone.utc)
    end = start + timedelta(days=5)
    days = [(start + timedelta(days=index)).date().isoformat() for index in range(5)]
    clock = {day: "no_direct_anchor" for day in days}
    protocol = {"raw_signal_ids": ["canal1_1"], "start_utc": start.isoformat(),
                "end_utc": end.isoformat(), "tapes": {"XAUUSD": [], "EURUSD": []},
                "currency_digits": 2, "contract_size": 100.0, "fx_max_age_ms": 5000,
                "clock_admitted": False, "clock_by_source_day": clock,
                "broker_epoch_offset_seconds_hypothesis": weekly.OFFSET_SECONDS,
                "raw_semantics_complete": True}
    deals = {"query_start": (start - timedelta(hours=4)).isoformat(),
             "query_end": (end + timedelta(hours=4)).isoformat(),
             "captured_at_utc": (end + timedelta(hours=4)).isoformat(),
             "server": "demo", "currency": "EUR", "deals": []}
    money = {"contract": "native_closed_money_anchor_v2", "positions": []}
    baskets = {"baskets": [], "positions": []}
    broker = {"account": {"server": "demo", "currency": "EUR"}, "conversion": {}}
    anchor = {"contract": "native_tick_anchor_diagnostic_v1", "clock_admitted": False,
              "scope": {"source_epoch_start": start.isoformat(),
                        "source_epoch_end_exclusive": end.isoformat()},
              "independent_clock_evidence": {"days": {day: {"status": status}
                                                       for day, status in clock.items()}}}
    deal_path = write("deals.json", deals)
    money_path = write("money.json", money)
    basket_path = write("baskets.json", baskets)
    broker_path = write("broker.json", broker)
    anchor_path = write("anchor.json", anchor)
    source_paths = (deal_path, money_path, basket_path, broker_path, anchor_path)
    source_hashes = {str(path): digest(path) for path in source_paths}
    protocol["input_paths_sha256"] = {str(path): digest(path)
                                      for path in (broker_path, anchor_path)}
    no_fill = account.no_fill_coverage(protocol, deals, money, broker)
    empty = compare_sequences([], [])
    sequence = {"inputs_sha256": {str(path): digest(path) for path in source_paths[:3]},
                "native_basket_count": 0, "native_baskets_missing_from_raw": [],
                "raw_signals_without_native_basket": ["canal1_1"],
                "verified_no_xau_fill_signal_ids": ["canal1_1"],
                "no_fill_coverage": no_fill,
                "rows": [{"signal_id": "canal1_1", "scenario": scenario,
                          "observed_position_count": 0,
                          "observed_fill_status": "verified_no_xau_fill_in_captured_deals",
                          "comparison": empty} for scenario in weekly.SCENARIOS]}
    native = {"contract": account.NATIVE_CONTRACT, "basket_count": 0,
              "observed_account_equity_compared": False,
              "independent_replay_compared": False,
              "per_basket_metrics_matched": 0, "per_basket_metrics_mismatched": [],
              "strict_causal": {"status": "exact_sampled_path_only"},
              "retrospective_bracketed": {"status": "exact_sampled_path_only"},
              "source_clock_admitted": False, "tape_proofs": protocol["tapes"],
              "inputs_sha256": source_hashes, "sources_sha256": {},
              "retrospective_fx_interval_ms": 60_000}
    protocol_path = write("protocol.json", protocol)
    assembled_path = write("assembled.json", {})
    sequence_path = write("sequence.json", sequence)
    native_path = write("native.json", native)
    paths = protocol_path, assembled_path, sequence_path
    hashes = {str(path): digest(path) for path in paths}
    monkeypatch.setattr(account, "_inputs", lambda *args: (
        paths, hashes, protocol, sequence))
    monkeypatch.setattr(account.weekly, "_load_tape", lambda _: ())

    output = tmp_path / "account.json"
    result = account.compare(*paths, native_path, output)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert result["signals"] == 1
    assert report["native_observed_sequence_rows_rechecked"] == len(weekly.SCENARIOS)
    assert len(report["rows"]) == len(weekly.SCENARIOS)
    assert report["shared_account_decisions_simulated"] is False
    assert report["cross_signal_decision_parity_verified"] is False
    assert report["observed_overlap_requires_shared_decision_audit"] is False
    assert report["risk_scope"] == "aggregated_isolated_signal_outputs_common_tick_grid"
    assert report["clock_unanchored_days"] == days
    assert report["native_signal_overlap"]["same_channel_overlap_pair_count"] == 0
    assert all(row["strict_causal"]["observed"]["max_drawdown_eur"] == "0.00"
               for row in report["rows"])

    protocol["input_paths_sha256"][str(anchor_path)] = "0" * 64
    with pytest.raises(ValueError, match="clock/money source differ"):
        account.compare(*paths, native_path, tmp_path / "bad_anchor.json")
    protocol["input_paths_sha256"][str(anchor_path)] = digest(anchor_path)
    protocol["clock_by_source_day"][days[0]] = "direct_anchor_available"
    with pytest.raises(ValueError, match="clock status differs"):
        account.compare(*paths, native_path, tmp_path / "bad_clock.json")
