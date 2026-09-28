from datetime import datetime, timedelta, timezone

import pytest

from research.causal_comparison import SequenceEvent
from research.causal_replay import CausalSignal
from tools import compare_week_causal_sequences as comparison
from tools import run_week_causal_controls as weekly


def _fixtures():
    at = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
    signal = CausalSignal("canal2_3086", "canal2", "BUY", at, at, "revision")
    absent = CausalSignal("canal1_22652", "canal1", "SELL", at, at, "other")
    virtual = {
        "blockers": [],
        "entries": [{"ticket": "sim_1", "source": "independent", "opened_at": at.isoformat(),
                     "entry_price": "100", "volume": "0.01"}],
        "exits": [{"ticket": "sim_1", "closed_at": (at + timedelta(seconds=1)).isoformat(),
                   "exit_price": "101", "volume": "0.01", "pnl_eur": "1.00",
                   "reason": "initial_tp"}],
    }
    controls = [{"signal_id": signal_id, **scenario, "status": "diagnostic_only",
                 "censored_data_end": False, "engine_mismatches": {"scalar": [], "fast": []},
                 "result": virtual}
                for signal_id in (signal.signal_id, absent.signal_id)
                for scenario in weekly.SCENARIOS]
    actual = [SequenceEvent(1, "entry", at, "BUY", 100, "0.01", 0, "market"),
              SequenceEvent(1, "exit", at + timedelta(seconds=1), "BUY", 101,
                            "0.01", "1.00", "tp")]
    return [signal, absent], controls, actual


def test_week_matrix_keeps_all_signals_and_scenarios_including_missing_native():
    signals, controls, actual = _fixtures()
    rows = comparison.compare_matrix(
        {"raw_signal_ids": [signal.signal_id for signal in signals]},
        {"results": controls}, {signal.signal_id: signal for signal in signals},
        {signals[0].signal_id: actual})
    assert len(rows) == 2 * len(weekly.SCENARIOS)
    assert all(row["comparison"]["status"] == "exact_facts_only"
               for row in rows if row["signal_id"] == signals[0].signal_id)
    assert all(row["comparison"]["status"] == "blocked"
               for row in rows if row["signal_id"] == signals[1].signal_id)
    assert all("no-trade not independently verified" in row["comparison"]["blockers"][0]
               for row in rows if row["signal_id"] == signals[1].signal_id)


def test_week_matrix_compares_verified_zero_native_fills_without_inventing_attempts():
    signals, controls, actual = _fixtures()
    rows = comparison.compare_matrix(
        {"raw_signal_ids": [signal.signal_id for signal in signals]},
        {"results": controls}, {signal.signal_id: signal for signal in signals},
        {signals[0].signal_id: actual, signals[1].signal_id: []})
    absent = [row for row in rows if row["signal_id"] == signals[1].signal_id]
    assert len(absent) == len(weekly.SCENARIOS)
    assert all(row["observed_position_count"] == 0 for row in absent)
    assert all(row["observed_fill_status"] == "verified_no_xau_fill_in_captured_deals"
               for row in absent)
    assert all(row["comparison"]["status"] == "mismatch" for row in absent)
    assert all("missing_event" in row["comparison"]["first_divergence"]["differences"]
               for row in absent)


def test_week_matrix_preserves_mismatch_and_censored_case():
    signals, controls, actual = _fixtures()
    controls[0]["result"] = {**controls[0]["result"], "entries": [
        {**controls[0]["result"]["entries"][0], "entry_price": "100.50"}]}
    controls[1]["status"] = "censored_data_end"
    controls[1]["censored_data_end"] = True
    rows = comparison.compare_matrix(
        {"raw_signal_ids": [signal.signal_id for signal in signals]},
        {"results": controls}, {signal.signal_id: signal for signal in signals},
        {signals[0].signal_id: actual})
    assert rows[0]["comparison"]["status"] == "mismatch"
    assert rows[0]["comparison"]["first_divergence"]["differences"] == ["price"]
    assert rows[1]["comparison"]["status"] == "blocked"
    assert "incomplete" in rows[1]["comparison"]["blockers"][0]


def test_week_matrix_rejects_missing_or_duplicate_controls():
    signals, controls, actual = _fixtures()
    args = ({"raw_signal_ids": [signal.signal_id for signal in signals]},
            {"results": controls[:-1]},
            {signal.signal_id: signal for signal in signals},
            {signals[0].signal_id: actual})
    with pytest.raises(ValueError, match="incomplete"):
        comparison.compare_matrix(*args)
    args[1]["results"] = [*controls, controls[0]]
    with pytest.raises(ValueError, match="duplicated"):
        comparison.compare_matrix(*args)


def test_observed_week_leg_uses_comment_identity_and_broker_exit_reason():
    entry_msc = 1789646400000
    deals = {"deals": [
        {"ticket": 10, "position_id": 20, "symbol": "XAUUSD", "entry": 0,
         "type": 0, "time_msc": entry_msc, "price": 100, "volume": 0.01,
         "profit": 0, "commission": 0, "swap": 0, "fee": 0,
         "comment": "c2_3086_g55", "reason": 3},
        {"ticket": 11, "position_id": 20, "symbol": "XAUUSD", "entry": 1,
         "type": 1, "time_msc": entry_msc + 1000, "price": 101, "volume": 0.01,
         "profit": 1, "commission": 0, "swap": 0, "fee": 0,
         "comment": "[tp 101]", "reason": 5},
    ]}
    positions = [{"position_id": 20, "deal_tickets": [10, 11], "actual_net_eur": "1.00"}]
    events = comparison._observed("canal2_3086", positions, deals)
    assert [(event.slot, event.kind, event.mechanism) for event in events] == [
        (1, "entry", "market"), (1, "exit", "tp")]
    deals["deals"][0]["comment"] = "c2_9999_g55"
    with pytest.raises(ValueError, match="not bound"):
        comparison._observed("canal2_3086", positions, deals)


def test_absent_fill_proof_requires_full_query_and_exhaustive_xau_deals():
    protocol = {"start_utc": "2026-09-14T00:00:00+00:00",
                "end_utc": "2026-09-19T00:00:00+00:00"}
    deals = {"query_start": "2026-09-13T00:00:00+00:00",
             "query_end": "2026-09-20T00:00:00+00:00",
             "captured_at_utc": "2026-09-19T12:00:00+00:00",
             "server": "demo", "currency": "EUR",
             "deals": [{"ticket": 10, "position_id": 20, "symbol": "XAUUSD"},
                       {"ticket": 11, "position_id": 20, "symbol": "XAUUSD"},
                       {"ticket": 12, "position_id": 0, "symbol": ""}]}
    money = {"positions": [{"position_id": 20, "deal_tickets": [10, 11]}]}
    broker = {"account": {"server": "demo", "currency": "EUR"}}
    proof = comparison.no_fill_coverage(protocol, deals, money, broker)
    assert proof["verified_no_xau_fill_for_absent_signal_ids"] is True
    assert proof["order_attempt_absence_verified"] is False
    assert proof["xau_deal_count"] == 2

    deals["captured_at_utc"] = "2026-09-18T23:00:00+00:00"
    assert comparison.no_fill_coverage(protocol, deals, money, broker)[
        "verified_no_xau_fill_for_absent_signal_ids"] is False
    deals["captured_at_utc"] = "2026-09-19T12:00:00+00:00"
    deals["deals"].append({"ticket": 13, "position_id": 30, "symbol": "XAUUSD"})
    assert comparison.no_fill_coverage(protocol, deals, money, broker)[
        "verified_no_xau_fill_for_absent_signal_ids"] is False
