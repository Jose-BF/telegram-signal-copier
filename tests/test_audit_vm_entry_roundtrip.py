"""Native deal identity and MT5 call timing are distinct evidence gates."""

from copy import deepcopy

import pytest

from tools.audit_vm_entry_roundtrip import OFFSET_MS, bind_signal, utc_ms


def fixture_rows():
    native_at = utc_ms("2026-09-17T12:31:07.803+00:00")
    comparison = {"signal_id": "canal2_3086", "channel": "canal2",
                  "first_native_entry_source_msc": native_at + OFFSET_MS,
                  "first_virtual_fill_utc_msc": utc_ms("2026-09-17T12:30:00.192+00:00"),
                  "first_virtual_fill_price": 4378.98}
    position = {"position_id": 10}
    deal = {"ticket": 123, "position_id": 10, "entry": 0, "symbol": "XAUUSD",
            "time_msc": native_at + OFFSET_MS, "price": 4376.56, "volume": 0.04}
    common = {"action_id": "action", "attempt_id": "attempt", "decision_id": "decision",
              "session_id": "session"}
    window = {"signal_id": "canal2_3086", "source_window_sha256": "abc", "events": [
        {"ev": "signal_received", "session_id": "session"},
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
    return comparison, window, position, deal


def test_exact_deal_is_bound_to_same_action_and_mt5_call():
    row = bind_signal(*fixture_rows())
    assert row["status"] == "exact_opening_deal_and_call_bound"
    assert row["native_entry_minus_virtual_tick_ms"] == 67_611
    assert row["mt5_order_send_roundtrip_ms"] == "67547"
    assert row["native_minus_call_roundtrip_residual_ms"] == "64"


def test_native_millisecond_clock_anomaly_is_retained_not_tolerated_away():
    comparison, window, position, deal = fixture_rows()
    native_at = utc_ms("2026-09-17T12:30:00.444+00:00")
    comparison["first_native_entry_source_msc"] = native_at + OFFSET_MS
    deal["time_msc"] = native_at + OFFSET_MS
    row = bind_signal(comparison, window, position, deal)
    assert row["status"] == "bound_with_native_call_clock_order_anomaly"
    assert row["native_entry_minus_call_start_ms"] == -18


def test_same_time_without_same_action_or_deal_is_rejected():
    comparison, window, position, deal = fixture_rows()
    wrong = deepcopy(window)
    wrong["events"][-1]["deal"] = 999
    with pytest.raises(ValueError, match="exact VM result"):
        bind_signal(comparison, wrong, position, deal)
    wrong = deepcopy(window)
    wrong["events"][2]["action_id"] = "other"
    with pytest.raises(ValueError, match="unique request/attempt"):
        bind_signal(comparison, wrong, position, deal)
