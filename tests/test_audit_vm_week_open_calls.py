"""A weekly MT5 call is bound by deal and action, with clock status explicit."""

from copy import deepcopy

import pytest

from tools.audit_vm_week_open_calls import bind_opening


def fixture_rows():
    basket = {"signal_id": "canal2_3086", "channel": "canal2"}
    position = {"signal_id": "canal2_3086", "position_id": 10,
                "first_native_msc": 1_789_659_067_803}
    deal = {"ticket": 123, "position_id": 10, "entry": 0, "symbol": "XAUUSD",
            "time_msc": position["first_native_msc"], "price": 4376.56, "volume": 0.04}
    receipt = {"sig": "canal2_3086", "session_id": "session", "event_id": "receipt",
               "ts": "2026-09-17T12:28:53.250+00:00"}
    common = {"action_id": "action", "attempt_id": "attempt", "decision_id": "decision",
              "session_id": "session"}
    window = {"signal_id": "canal2_3086", "source_window_sha256": "abc", "events": [
        {"ev": "mt5_order_requested", **common, "event_id": "request",
         "ts": "2026-09-17T12:30:00.462+00:00", "requested_price": 4378.98},
        {"ev": "mt5_action_attempt", **common, "event_id": "attempt",
         "operation": "OPEN_MARKET", "broker_request_sent": True, "filled_price": 4376.56,
         "broker_request_started_utc": "2026-09-17T12:30:00.462+00:00",
         "broker_response_received_utc": "2026-09-17T12:31:08.014+00:00",
         "broker_roundtrip_ns": 67_547_000_000,
         "pre_broker_duration_ns": 0, "post_broker_duration_ns": 0},
        {"ev": "mt5_order_result", **common, "event_id": "result",
         "ts": "2026-09-17T12:31:08.014+00:00", "deal": 123,
         "price": 4376.56, "volume": 0.04, "retcode": 10009},
    ]}
    return basket, position, deal, window, receipt


def test_direct_clock_call_and_strategy_wait_are_separate():
    row = bind_opening("canal2_3086", *fixture_rows(), "direct_anchor_available")
    assert row["status"] == "bound_direct_clock_order"
    assert row["receipt_to_request_ms"] == 67_212
    assert row["mt5_order_send_roundtrip_ms"] == "67547"
    assert row["native_entry_minus_call_start_ms_hypothesis"] == 67_341


def test_unanchored_day_keeps_clock_hypothesis():
    row = bind_opening("canal2_3086", *fixture_rows(), "no_direct_anchor")
    assert row["status"] == "bound_broker_clock_hypothesis"
    assert row["direct_clock_anchor"] is False


def test_mismatched_deal_or_action_cannot_bind():
    basket, position, deal, window, receipt = fixture_rows()
    wrong = deepcopy(window)
    wrong["events"][-1]["deal"] = 999
    with pytest.raises(ValueError, match="exact result"):
        bind_opening("canal2_3086", basket, position, deal, wrong, receipt, "direct_anchor_available")
    wrong = deepcopy(window)
    wrong["events"][1]["action_id"] = "other"
    with pytest.raises(ValueError, match="uniquely bound"):
        bind_opening("canal2_3086", basket, position, deal, wrong, receipt, "direct_anchor_available")
