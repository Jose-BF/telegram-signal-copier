import copy

import pytest

from tools.audit_first_entry_roundtrip import audit_first_entries


def fixtures():
    first = {"model_requested_utc": "1970-01-01T00:00:01.041000+00:00",
             "native_fill_utc": "1970-01-01T00:00:01+00:00",
             "model_minus_native_ms": 41,
             "native_entry_price": "4361.07", "native_entry_volume": "0.01",
             "model_minus_native_price": "-0.02", "same_entry_volume": True}
    report = {"contract": "incremental_common_tick_path_comparison_v1",
              "status": "diagnostic_only", "full_live_parity_verified": False,
              "counts": {"path_discrepant": 1},
              "rows": [{"signal_id": "canal1_a", "channel": "canal1",
                        "status": "path_discrepant", "first_entry_evidence": first}]}
    terminal = {"status": "diagnostic_only", "rows": [
        {"signal_id": "canal1_a", "status": "terminal_deal_and_done_bound",
         "native_entry_deal": 7, "terminal_stage_order_consistent": True,
         "call_clock_order_consistent": False,
         "terminal_market_minus_python_call_ms": 2,
         "market_to_accepted_ms": 122, "accepted_to_deal_ms": 0,
         "terminal_deal_minus_python_start_ms": 124,
         "python_mt5_call_ms": "125"}]}
    money = {"account_currency": "EUR", "positions": [
        {"signal_id": "canal1_a", "entry_msc": 10_801_000,
         "entry_price": "4361.07", "volume": "0.01", "deal_tickets": [7, 8]}]}
    return report, terminal, money


def test_audit_links_first_entry_to_observed_terminal_deal():
    report, terminal, money = fixtures()
    result = audit_first_entries([report], terminal, money)
    assert result["matched_basket_count"] == 1
    assert result["input_basket_row_count"] == 1
    assert result["excluded_statuses"] == {}
    assert result["model_request_from_python_start_min_ms"] == 165
    assert result["rows"][0]["terminal_market_to_accepted_ms"] == 122
    assert result["rows"][0]["terminal_cross_clock_order_consistent"] is False
    assert result["full_live_path_parity_verified"] is False


def test_audit_rejects_wrong_deal_stage_and_duplicate_basket():
    report, terminal, money = fixtures()
    wrong_deal = copy.deepcopy(terminal)
    wrong_deal["rows"][0]["native_entry_deal"] = 9
    with pytest.raises(ValueError, match="first native deal"):
        audit_first_entries([report], wrong_deal, money)
    wrong_stage = copy.deepcopy(terminal)
    wrong_stage["rows"][0]["accepted_to_deal_ms"] = 1
    with pytest.raises(ValueError, match="stages inconsistent"):
        audit_first_entries([report], wrong_stage, money)
    wrong_call = copy.deepcopy(terminal)
    wrong_call["rows"][0]["python_mt5_call_ms"] = "NaN"
    with pytest.raises(ValueError, match="stages inconsistent"):
        audit_first_entries([report], wrong_call, money)
    with pytest.raises(ValueError, match="duplicate first-entry basket"):
        audit_first_entries([report, report], terminal, money)
    wrong_denominator = copy.deepcopy(report)
    wrong_denominator["counts"] = {"path_discrepant": 2}
    with pytest.raises(ValueError, match="row denominator"):
        audit_first_entries([wrong_denominator], terminal, money)
