from datetime import datetime, timezone

import pytest

from tools.audit_week_lifecycle_controls import audit


BASE = datetime(2026, 9, 17, 14, tzinfo=timezone.utc)
START = "2026-09-14T00:00:00+00:00"
END = "2026-09-19T00:00:00+00:00"


def text_row(message_id, at):
    return {"ev": "telegram_raw", "channel": "canal1", "message_id": message_id,
            "message_revision_id": f"rev-{message_id}", "date_utc": at,
            "ts": at, "text": "BUY GOLD NOW 4366 TP1: 4370 SL: 4355",
            "is_edit": False, "edit_date_utc": None, "reply_to_msg_id": None,
            "sticker_id": None}


def fixtures():
    source = "a" * 64
    first = BASE.isoformat()
    second = BASE.replace(minute=20).isoformat()
    raw = {"source_slice_sha256": source, "start_offset": 10, "end_offset": 100,
           "start_utc": START, "end_utc": END,
           "rows": [text_row(2, first), text_row(3, second)]}
    lifecycle = {"source_slice_sha256": source, "start_offset": 10,
                 "end_offset": 100, "start_utc": START, "end_utc": END,
                 "event_count": 6, "events": [
                     {"ev": "signal_received", "sig": "canal1_1", "ts": first},
                     {"ev": "canal1_text_processing", "sig": "canal1_1",
                      "source_msg_id": 2, "ts": first},
                     {"ev": "signal_closed", "sig": "canal1_1",
                      "ts": BASE.replace(minute=15).isoformat()},
                     {"ev": "signal_received", "sig": "canal1_3",
                      "trigger": "text_only", "ts": second},
                     {"ev": "canal1_text_processing", "sig": "canal1_3",
                      "source_msg_id": 3, "ts": second},
                     {"ev": "signal_closed", "sig": "canal1_3",
                      "ts": BASE.replace(minute=30).isoformat()},
                 ]}
    native = {"currency": "EUR", "baskets": [
        {"signal_id": "canal1_1", "channel": "canal1",
         "last_native_calendar": "2026-09-17T17:14:55"}]}
    anchor = {"scope": {"currency": "EUR"}, "independent_clock_evidence": {
        "days": {"2026-09-17": {"status": "direct_anchor_available"}}}}
    return raw, lifecycle, native, anchor


def test_audit_retains_observed_text_routes_and_clock_qualified_close_lag():
    report = audit(*fixtures())
    assert report["received_signal_count"] == 2
    assert report["closed_received_signal_count"] == 2
    assert report["fresh_text_candidate_count"] == 2
    assert report["text_route_counts"] == {
        "processed_against_existing": 1, "text_only_received": 1}
    assert report["text_routes"][0]["target_signal_id"] == "canal1_1"
    assert report["text_routes"][1]["target_signal_id"] == "canal1_3"
    first = next(row for row in report["signals"] if row["signal_id"] == "canal1_1")
    assert first["native_to_signal_close_lag_s_hypothesis"] == 5.0
    assert first["direct_clock_anchor_available"] is True


def test_audit_preserves_missing_close_and_rejects_unbound_sources():
    raw, lifecycle, native, anchor = fixtures()
    lifecycle["events"] = lifecycle["events"][:-1]
    lifecycle["event_count"] -= 1
    report = audit(raw, lifecycle, native, anchor)
    assert report["received_without_close_in_slice"] == ["canal1_3"]
    assert report["closed_received_signal_count"] == 1
    lifecycle["source_slice_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="source slice"):
        audit(raw, lifecycle, native, anchor)
