import json
from pathlib import Path

import pytest

from tools.audit_journal_chunk_index import scan_chunk
from tools.probe_vm_signal_lifecycle import (build_window, extract, extract_material,
                                             selected_fields, selected_management_fields)


def test_attempt_export_keeps_broker_identity_without_private_result_fields():
    row = {"ev": "mt5_action_attempt", "result": {"retcode": 10009, "deal": 123,
           "order": 456, "price": 4369.25, "volume": 0.02, "comment": "private"},
           "request": {"action": 1, "position": 789, "volume": 0.02,
                       "comment": "private"}}
    exported = selected_fields(row)
    assert {key: exported[key] for key in ("result_deal", "result_order", "result_price",
                                            "result_volume", "request_action", "request_position",
                                            "request_volume")} == {
        "result_deal": 123, "result_order": 456, "result_price": 4369.25,
        "result_volume": 0.02, "request_action": 1, "request_position": 789,
        "request_volume": 0.02,
    }
    assert "comment" not in exported and "result" not in exported and "request" not in exported
    row["result"]["deal"] = True
    assert selected_fields(row)["result_deal"] is None


def test_snapshot_export_preserves_explicit_position_existence():
    base = {"ev": "mt5_position_snapshot", "ticket": 101, "tp": 4356.37,
            "secret": "never export"}
    for exists in (True, False, None):
        row = {**base, "position_exists": exists}
        exported = selected_fields(row)
        assert "position_exists" in exported
        assert exported["position_exists"] is exists
        assert "secret" not in exported


def test_snapshot_export_preserves_native_price_and_identity():
    row = {"ev": "mt5_position_snapshot", "ticket": 101, "position_exists": True,
           "symbol": "XAUUSD", "magic": 555, "position_type": 0,
           "price_open": 4355.87, "price_current": 4356.37,
           "profit": 1.75, "comment": "gold_now_555_v1"}
    exported = selected_fields(row)
    for key in ("position_exists", "symbol", "magic", "position_type",
                "price_open", "price_current", "profit", "comment"):
        assert exported[key] == row[key]
    other = selected_fields({**row, "ev": "mt5_order_result"})
    assert not {"symbol", "magic", "position_type", "price_open",
                "price_current", "comment"} & other.keys()


def test_snapshot_export_preserves_read_interval_for_open_and_closed_only():
    interval = {"positions_read_started_utc": "2026-09-18T12:00:00.001000+00:00",
                "positions_read_completed_utc": "2026-09-18T12:00:00.002000+00:00",
                "positions_read_elapsed_ms": 1.0}
    for exists in (True, False, None):
        row = {"ev": "mt5_position_snapshot", "position_exists": exists, **interval}
        exported = selected_fields(row)
        assert all(exported[key] == value for key, value in interval.items())
        other = selected_fields({**row, "ev": "mt5_order_result"})
        assert not interval.keys() & other.keys()


def test_bounded_snapshot_probe_retains_existence_value(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    row = {"ts": "2026-09-18T12:00:00+00:00", "sig": "canal2_3171",
           "ev": "mt5_position_snapshot", "ticket": 101,
           "position_exists": True, "tp": 4356.37}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    spec = {"source": str(path), "scope_start_offset": 0, "scope_end_offset": path.stat().st_size,
            "window": {"signal_id": "canal2_3171", "start_utc": "2026-09-18T11:59:45+00:00",
                       "end_utc": "2026-09-18T12:05:00+00:00"}}
    result = extract(spec)
    assert result["event_counts"]["mt5_position_snapshot"] == 1
    assert result["events"][0]["position_exists"] is True
    assert "position_exists" in result["field_names_by_kind"]["mt5_position_snapshot"]


def test_management_leg_inputs_accept_historical_optional_current_sl_only():
    row = {"ev": "bot_internal_decision_started", "management_contract": "management_decision_inputs_v1",
           "management_kind": "gold_555_leg_protection", "decision_inputs": {
               "ticket": 101, "fill_price": 4300.0, "leg_index": 0},
           "state_before": {"status": "active"}}
    assert selected_management_fields(row)["decision_inputs"]["ticket"] == 101
    row["decision_inputs"]["current_sl"] = 4280.0
    assert selected_management_fields(row)["decision_inputs"]["current_sl"] == 4280.0
    row["decision_inputs"]["message_text"] = "private"
    with pytest.raises(ValueError, match="schema mismatch"):
        selected_management_fields(row)


def test_management_guard_projection_keeps_bounded_position_read_interval():
    row = {"ev": "bot_internal_decision_started",
           "management_contract": "management_decision_inputs_v1",
           "management_kind": "gold_555_basket_guard",
           "decision_inputs": {"summary": {
               "positions_read_started_utc": "2026-09-18T11:59:59.500+00:00",
               "positions_read_completed_utc": "2026-09-18T11:59:59.700+00:00",
               "positions_read_elapsed_ms": 200.0},
               "now_utc": "2026-09-18T12:00:00+00:00"},
           "state_before": {}}
    selected = selected_management_fields(row)
    assert selected["decision_inputs"]["summary"]["positions_read_elapsed_ms"] == 200.0


def test_build_window_uses_all_native_deals_and_direct_clock():
    calls = {"rows": [{"signal_id": "canal2_3086", "direct_clock_anchor": True,
                       "receipt_utc": "2026-09-18T12:00:00+00:00"}]}
    baskets = {"positions": [{"signal_id": "canal2_3086", "deal_tickets": [1, 2]}]}
    native = {"deals": [{"ticket": 1, "time_msc": 1789740000000},
                        {"ticket": 2, "time_msc": 1789740240000}]}
    with pytest.raises(ValueError, match="outside bounded"):
        build_window("canal2_3086", calls, baskets, native)
    native["deals"][0]["time_msc"] = 1789743600000
    native["deals"][1]["time_msc"] = 1789743840000
    window = build_window("canal2_3086", calls, baskets, native)
    assert window["native_deal_tickets"] == [1, 2]
    assert window["start_utc"] == "2026-09-18T11:59:45.000+00:00"
    assert window["end_utc"] == "2026-09-18T12:04:30.000+00:00"
    calls["rows"][0]["direct_clock_anchor"] = False
    with pytest.raises(ValueError, match="direct-clock"):
        build_window("canal2_3086", calls, baskets, native)


def test_extract_bounded_identity_and_allowlist(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    rows = [
        {"ts": "2026-09-18T12:00:00+00:00", "sig": "canal2_3086", "ev": "signal_received",
         "message_text": "private signal"},
        {"ts": "2026-09-18T12:01:00+00:00", "sig": "canal2_3086", "ev": "mt5_order_result",
         "deal": 123, "order": 456, "secret": "never export"},
        {"ts": "2026-09-18T12:01:05+00:00", "sig": "canal2_3086", "ev": "bot_internal_decision",
         "management_kind": "gold_555", "decision_status": "no_action"},
        {"ts": "2026-09-18T12:01:06+00:00", "sig": "canal2_3086", "ev": "mt5_action_attempt",
         "operation": "MODIFY_SLTP", "preflight_status": "ready", "broker_request_sent": True,
         "result": {"retcode": 10016, "comment": "private"}, "request": {"sl": 123.4}},
        {"ts": "2026-09-18T12:01:10+00:00", "sig": "canal2_9999", "ev": "mt5_order_result"},
        {"ts": "2026-09-18T12:10:00+00:00", "sig": "canal2_3086", "ev": "outside"},
    ]
    path.write_bytes(b"".join((json.dumps(row) + "\n").encode() for row in rows))
    spec = {"source": str(path), "scope_start_offset": 0, "scope_end_offset": path.stat().st_size,
            "window": {"signal_id": "canal2_3086", "start_utc": "2026-09-18T11:59:45+00:00",
                       "end_utc": "2026-09-18T12:05:00+00:00"}}
    result = extract(spec)
    assert result["event_counts"] == {"signal_received": 1, "mt5_order_result": 1,
                                      "bot_internal_decision": 1, "mt5_action_attempt": 1}
    assert result["events"][1]["deal"] == 123
    assert "secret" not in result["events"][1]
    assert "message_text" not in result["events"][0]
    assert "secret" in result["field_names_by_kind"]["mt5_order_result"]
    assert result["decision_status_counts"] == [{"management_kind": "gold_555",
                                                   "decision_status": "no_action", "count": 1}]
    assert result["events"][3]["result_retcode"] == 10016
    assert result["events"][3]["request_sl"] == 123.4
    assert "comment" not in result["events"][3]
    assert result["scanned_bytes"] == path.stat().st_size


def test_extract_counts_high_frequency_kind_without_claiming_full_records(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    rows = [{"ts": "2026-09-18T12:00:00+00:00", "sig": "canal2_3086",
             "ev": "floating_pl_snapshot", "profit": index} for index in range(105)]
    path.write_bytes(b"".join((json.dumps(row) + "\n").encode() for row in rows))
    spec = {"source": str(path), "scope_start_offset": 0, "scope_end_offset": path.stat().st_size,
            "window": {"signal_id": "canal2_3086", "start_utc": "2026-09-18T11:59:45+00:00",
                       "end_utc": "2026-09-18T12:05:00+00:00"}}
    result = extract(spec)
    assert result["event_counts"] == {"floating_pl_snapshot": 105}
    assert len(result["events"]) == 100
    assert result["sampled_kinds"] == {"floating_pl_snapshot": 105}


def test_material_segment_keeps_rejections_and_action_decisions(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    rows = [
        {"ts": "2026-09-18T12:00:00+00:00", "sig": "canal2_3086", "ev": "bot_internal_decision_started"},
        {"ts": "2026-09-18T12:00:01+00:00", "sig": "canal2_3086", "ev": "bot_internal_decision",
         "decision_status": "completed", "declared_action_count": 0},
        {"ts": "2026-09-18T12:00:02+00:00", "sig": "canal2_3086", "ev": "bot_internal_decision",
         "decision_status": "completed", "declared_action_count": 1,
         "declared_action_ids": ["action-1"]},
        {"ts": "2026-09-18T12:00:03+00:00", "sig": "canal2_3086", "ev": "mt5_action_attempt",
         "operation": "MODIFY_SLTP", "result": {"retcode": 10016, "comment": "private"},
         "request": {"sl": 123.4}},
        {"ts": "2026-09-18T12:00:04+00:00", "sig": "canal1_22732", "ev": "dca_filled"},
        {"ts": "2026-09-18T12:06:00+00:00", "sig": "canal2_3086", "ev": "outside"},
    ]
    path.write_bytes(b"".join((json.dumps(row) + "\n").encode() for row in rows))
    spec = {"source": str(path), "scope_start_offset": 0, "scope_end_offset": path.stat().st_size,
            "segment": {"start_utc": "2026-09-18T12:00:00+00:00",
                        "end_utc": "2026-09-18T12:05:00+00:00",
                        "active_signals": ["canal1_22732", "canal2_3086"]}}
    result = extract_material(spec)
    assert [row["ev"] for row in result["events"]] == ["bot_internal_decision",
                                                       "mt5_action_attempt", "dca_filled"]
    assert result["events"][0]["declared_action_ids"] == ["action-1"]
    assert result["events"][1]["result_retcode"] == 10016
    assert "comment" not in result["events"][1]
    assert sum(row["count"] for row in result["event_counts"]) == 5
    assert sum(row["count"] for row in result["material_event_counts"]) == 3
    assert result["material_records_complete_within_segment"] is True


def test_indexed_material_chunk_reads_out_of_order_events_and_checks_hash(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    rows = [
        {"ts": "2026-09-18T12:00:00+00:00", "sig": "canal2_3086",
         "ev": "signal_received", "event_id": "first"},
        {"ts": "2026-09-18T14:00:00+00:00", "sig": "canal2_3086",
         "ev": "outside", "event_id": "outside"},
        {"ts": "2026-09-18T12:04:00+00:00", "sig": "canal2_3086",
         "ev": "mt5_order_result", "event_id": "late"},
    ]
    path.write_bytes(b"".join((json.dumps(row) + "\n").encode() for row in rows))
    chunk = scan_chunk(path, 0, path.stat().st_size, prefix_end=path.stat().st_size)
    spec = {"source": str(path), "scope_start_offset": chunk["actual_start"],
            "scope_end_offset": chunk["actual_end"], "indexed_chunk_sha256": chunk["sha256"],
            "segment": {"start_utc": "2026-09-18T12:00:00+00:00",
                        "end_utc": "2026-09-18T12:05:00+00:00",
                        "active_signals": ["canal2_3086"]}}
    report = extract_material(spec)
    assert report["contract"] == "bounded_vm_week_material_chunk_v3"
    assert [row["event_id"] for row in report["events"]] == ["first", "late"]
    assert report["records_complete_within_indexed_chunk"] is True
    assert report["material_records_complete_within_segment"] is False
    spec["indexed_chunk_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="index hash mismatch"):
        extract_material(spec)


def test_material_segment_retains_complete_management_no_op_inputs(tmp_path: Path, monkeypatch):
    path = tmp_path / "events.jsonl"
    common = {"sig": "canal2_3086", "session_id": "s", "decision_id": "d",
              "event_id": "e", "management_contract": "management_decision_inputs_v1",
              "management_kind": "gold_555_basket_guard", "strategy_id": "gold_now_555_v1",
              "strategy_fingerprint": "fingerprint", "direction": "BUY"}
    state = {"status": "active", "basket_guard_armed": False}
    rows = [
        {**common, "ev": "bot_internal_decision_started", "event_id": "start",
         "ts": "2026-09-18T12:00:00+00:00", "decision_inputs": {
             "summary": {"n_open": 1, "total_pl": 2.0, "open_tickets": [700]},
             "now_utc": "2026-09-18T12:00:00+00:00"}, "state_before": state},
        {**common, "ev": "bot_internal_decision", "event_id": "done",
         "ts": "2026-09-18T12:00:01+00:00", "decision_status": "completed",
         "declared_action_count": 0, "declared_action_ids": [],
         "decision_result": {"action": "none", "reason": None, "observed_pl": 2.0,
                             "state": {"armed": False, "triggered": False,
                                       "peak_pl": None, "trigger_reason": None,
                                       "recovery_pending": False}}, "state_after": state},
    ]
    path.write_bytes(b"".join((json.dumps(row) + "\n").encode() for row in rows))
    spec = {"source": str(path), "scope_start_offset": 0, "scope_end_offset": path.stat().st_size,
            "segment": {"start_utc": "2026-09-18T12:00:00+00:00",
                        "end_utc": "2026-09-18T12:05:00+00:00",
                        "active_signals": ["canal2_3086"]}}
    report = extract_material(spec)
    assert report["contract"] == "bounded_vm_week_material_segment_v2"
    assert report["events"] == []
    assert [row["ev"] for row in report["management_events"]] == [
        "bot_internal_decision_started", "bot_internal_decision"]
    assert report["management_event_counts"] == [
        {"signal_id": "canal2_3086", "event": "bot_internal_decision", "count": 1},
        {"signal_id": "canal2_3086", "event": "bot_internal_decision_started", "count": 1}]
    assert report["management_records_complete_within_segment"] is True
    monkeypatch.setattr("tools.probe_vm_signal_lifecycle.MAX_MANAGEMENT_EVENTS", 1)
    with pytest.raises(ValueError, match="management capture output budget exceeded"):
        extract_material(spec)
    rows[0]["decision_inputs"]["summary"]["message_text"] = "private"
    with pytest.raises(ValueError, match="schema mismatch"):
        selected_management_fields(rows[0])
