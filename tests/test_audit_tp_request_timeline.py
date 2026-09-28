from datetime import datetime, timedelta, timezone

import pytest

from tools.audit_tp_request_timeline import _bound_leg, summarize_tp_attempts


BASE = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
BASE_MS = int(BASE.timestamp() * 1000)


def attempt(start, response, retcode, *, target="4356.37", attempt_id="attempt"):
    return {"ev": "mt5_action_attempt", "operation": "MODIFY_SLTP",
            "ticket": 101, "attempt_id": attempt_id, "broker_request_sent": True,
            "broker_request_started_utc": (BASE + timedelta(milliseconds=start)).isoformat(),
            "broker_response_received_utc": (BASE + timedelta(milliseconds=response)).isoformat(),
            "request_tp": target, "result_retcode": retcode}


def test_tp_request_timeline_distinguishes_inflight_rejection_from_acceptance():
    rows = [attempt(100, 500, 10016, attempt_id="rejected"),
            attempt(800, 1400, 10009, attempt_id="accepted")]
    result = summarize_tp_attempts(rows, target="4356.37",
                                   tick_msc=BASE_MS + 200,
                                   emitted_msc=BASE_MS + 1500)
    assert result["request_in_flight_at_virtual_tick"] == ["rejected"]
    assert result["inflight_at_tick_later_rejected_count"] == 1
    assert result["target_accepted_response_before_virtual_tick"] is False
    assert result["target_accepted_response_before_shadow_emit"] is True
    assert result["first_target_accepted_response_minus_virtual_tick_ms"] == 1200
    assert result["first_target_accepted_response_minus_shadow_emit_ms"] == -100


def test_tp_request_timeline_keeps_other_confirmed_tp_visible():
    rows = [attempt(100, 200, 10009, target="4356.10", attempt_id="provisional"),
            attempt(300, 400, 10009, attempt_id="exact")]
    result = summarize_tp_attempts(rows, target="4356.37",
                                   tick_msc=BASE_MS + 250,
                                   emitted_msc=BASE_MS + 500)
    assert result["any_tp_accepted_response_before_virtual_tick"] is True
    assert result["target_accepted_response_before_virtual_tick"] is False
    assert result["target_accepted_response_before_shadow_emit"] is True


def test_tp_request_timeline_fails_closed_on_missing_or_invalid_attempts():
    with pytest.raises(ValueError, match="no TP-bearing"):
        summarize_tp_attempts([], target="4356.37",
                              tick_msc=BASE_MS + 200, emitted_msc=BASE_MS + 500)
    with pytest.raises(ValueError, match="response precedes request"):
        summarize_tp_attempts([attempt(500, 400, 10009)], target="4356.37",
                              tick_msc=BASE_MS + 200, emitted_msc=BASE_MS + 600)


def test_bound_leg_requires_initial_order_and_native_deal_identity():
    leg = {"leg_index": 0, "native_position_id": 101, "native_entry_deal": 201,
           "entry_price": "100.6", "volume": "0.04"}
    exit_row = {"leg_index": 0, "native_position_id": 101,
                "status": "broker_tp_exit_matches_fill_relative_target",
                "virtual_close_event_id": "close", "tp_snapshot_event_id": "snapshot",
                "observed_pre_exit_tp": "4356.37"}
    events = [
        {"ev": "mt5_order_result", "action_id": "entry", "attempt_id": "entry_attempt",
         "order": 101, "deal": 201, "price": 100.6, "volume": 0.04, "retcode": 10009},
        {"ev": "mt5_order_requested", "event_id": "order", "action_id": "entry",
         "attempt_id": "entry_attempt", "tp": None},
        {**attempt(100, 200, 10009), "ticket": 101},
    ]
    report = {"signal_id": "canal2_1", "snapshot_alignment": {"rows": [{
        "event_id": "snapshot",
        "broker_response_received_utc": (BASE + timedelta(milliseconds=200)).isoformat()}]}}
    close = {"event_id": "close", "sig": "canal2_1", "transition": "virtual_position_closed",
             "transition_tick_msc": BASE_MS + 300,
             "ts": (BASE + timedelta(milliseconds=400)).isoformat(),
             "transition_details": {"leg_indexes": [0]}}
    probe = {"events": events}
    bound = _bound_leg(probe, report, leg, exit_row, {"close": close})
    assert bound["initial_order_requested_tp"] is None
    assert bound["target_accepted_response_before_virtual_tick"] is True
    events[0]["deal"] = 202
    with pytest.raises(ValueError, match="contradicts native entry"):
        _bound_leg(probe, report, leg, exit_row, {"close": close})
    events[0]["deal"] = 201
    del events[1]["tp"]
    with pytest.raises(ValueError, match="initial TP request missing"):
        _bound_leg(probe, report, leg, exit_row, {"close": close})
