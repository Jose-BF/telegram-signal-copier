from copy import deepcopy
import json

import pytest

from tools.probe_vm_signal_lifecycle import extract
from tools.verify_lifecycle_probe_projection import compare_projection


def probes():
    old = {"contract": "bounded_vm_signal_lifecycle_probe_v1", "status": "diagnostic_only",
           "window": {"signal_id": "canal2_3171"},
           "source_window_sha256": "a" * 64, "scope_start_offset": 100,
           "scope_end_offset": 1000, "scanned_offset_start": 200,
           "scanned_offset_end": 800, "scanned_bytes": 600,
           "source_file_size_at_start": 1000,
           "event_counts": {"mt5_position_snapshot": 1, "mt5_order_result": 1},
           "sampled_kinds": {}, "field_names_by_kind": {
               "mt5_position_snapshot": ["ev", "position_exists", "tp"],
               "mt5_order_result": ["ev", "deal"]},
           "events": [{"ev": "mt5_order_result", "event_id": "order", "deal": 201},
                      {"ev": "mt5_position_snapshot", "event_id": "snapshot", "tp": 101.1}]}
    new = deepcopy(old)
    new["source_file_size_at_start"] = 1200
    new["events"][1]["position_exists"] = True
    return old, new


def test_reprojection_accepts_only_new_existence_value_on_identical_source():
    old, new = probes()
    result = compare_projection(old, new)
    assert result["status"] == "projection_verified"
    assert result["snapshot_count"] == 1
    assert result["position_exists_true"] == 1
    assert result["position_exists_false"] == 0
    assert result["position_exists_unknown"] == 0


def test_reprojection_blocks_changed_bytes_events_or_other_fields():
    old, new = probes()
    new["source_window_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="source window"):
        compare_projection(old, new)
    old, new = probes()
    new["events"][0]["deal"] = 202
    with pytest.raises(ValueError, match="old event field"):
        compare_projection(old, new)
    old, new = probes()
    new["events"][1]["sl"] = 99
    with pytest.raises(ValueError, match="unexpected added field"):
        compare_projection(old, new)


def test_reprojection_blocks_missing_or_truncated_existence():
    old, new = probes()
    del new["events"][1]["position_exists"]
    with pytest.raises(ValueError, match="position_exists missing"):
        compare_projection(old, new)
    old, new = probes()
    new["events"] = new["events"][:1]
    with pytest.raises(ValueError, match="event identity"):
        compare_projection(old, new)


def test_reprojection_preserves_false_and_unknown_separately():
    old, new = probes()
    old["event_counts"]["mt5_position_snapshot"] = 3
    new["event_counts"]["mt5_position_snapshot"] = 3
    for index, value in enumerate((False, None), start=2):
        old["events"].append({"ev": "mt5_position_snapshot", "event_id": f"snapshot{index}"})
        new["events"].append({"ev": "mt5_position_snapshot", "event_id": f"snapshot{index}",
                              "position_exists": value})
    result = compare_projection(old, new)
    assert result["snapshot_count"] == 3
    assert result["position_exists_true"] == 1
    assert result["position_exists_false"] == 1
    assert result["position_exists_unknown"] == 1


def detailed_probes():
    old, new = probes()
    fields = ["symbol", "magic", "position_type", "price_open", "price_current", "comment"]
    old["field_names_by_kind"]["mt5_position_snapshot"].extend(fields)
    new["field_names_by_kind"]["mt5_position_snapshot"].extend(fields)
    new["events"][1].update({"symbol": "XAUUSD", "magic": 555, "position_type": 0,
                             "price_open": 100.6, "price_current": 101.1,
                             "comment": "gold_now_555_v1"})
    return old, new


def test_detailed_reprojection_restores_native_values_without_changing_old_fields():
    old, new = detailed_probes()
    result = compare_projection(old, new, restore_native_details=True)
    assert result["contract"] == "lifecycle_probe_native_projection_verification_v2"
    assert result["native_open_position_detail_count"] == 1
    with pytest.raises(ValueError, match="unexpected added field"):
        compare_projection(old, new)


def test_detailed_reprojection_blocks_missing_or_invalid_native_fields():
    old, new = detailed_probes()
    del new["events"][1]["price_current"]
    with pytest.raises(ValueError, match="detail missing"):
        compare_projection(old, new, restore_native_details=True)
    old, new = detailed_probes()
    new["events"][1]["price_current"] = 0
    with pytest.raises(ValueError, match="detail invalid"):
        compare_projection(old, new, restore_native_details=True)
    old, new = detailed_probes()
    new["events"][0]["symbol"] = "XAUUSD"
    with pytest.raises(ValueError, match="unexpected added field"):
        compare_projection(old, new, restore_native_details=True)


def test_detailed_reprojection_preserves_closed_snapshot_without_details():
    old, new = detailed_probes()
    new["events"][1] = {"ev": "mt5_position_snapshot", "event_id": "snapshot",
                         "tp": 101.1, "position_exists": False}
    result = compare_projection(old, new, restore_native_details=True)
    assert result["native_open_position_detail_count"] == 0
    new["events"][1]["price_current"] = 101.1
    with pytest.raises(ValueError, match="closed or unknown"):
        compare_projection(old, new, restore_native_details=True)


def test_detailed_reprojection_accepts_real_extractor_projection_on_same_bytes(tmp_path):
    source = tmp_path / "journal.jsonl"
    events = [{"ts": "2026-09-18T12:00:01+00:00", "sig": "canal2_3171",
               "ev": "mt5_position_snapshot", "event_id": "open", "ticket": 101,
               "position_exists": True, "symbol": "XAUUSD", "magic": 555,
               "position_type": 0, "price_open": 4355.87,
               "price_current": 4356.37, "profit": 1.75, "comment": "gold"},
              {"ts": "2026-09-18T12:00:02+00:00", "sig": "canal2_3171",
               "ev": "mt5_position_snapshot", "event_id": "closed", "ticket": 101,
               "position_exists": False}]
    source.write_text("".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")
    new = extract({"source": str(source), "scope_start_offset": 0,
                   "scope_end_offset": source.stat().st_size,
                   "window": {"signal_id": "canal2_3171",
                              "start_utc": "2026-09-18T12:00:00+00:00",
                              "end_utc": "2026-09-18T12:01:00+00:00"}})
    old = deepcopy(new)
    for row in old["events"]:
        for key in ("position_exists", "symbol", "magic", "position_type",
                    "price_open", "price_current", "comment"):
            row.pop(key, None)
    verified = compare_projection(old, new, restore_native_details=True)
    assert verified["source_window_sha256"] == new["source_window_sha256"]
    assert verified["snapshot_count"] == 2
    assert verified["native_open_position_detail_count"] == 1
    assert verified["position_exists_false"] == 1


def test_schema_expansion_requires_source_inventory_and_preserves_old_fields():
    old, new = detailed_probes()
    old["events"][0]["ev"] = "mt5_action_attempt"
    new["events"][0]["ev"] = "mt5_action_attempt"
    del old["event_counts"]["mt5_order_result"]
    old["event_counts"]["mt5_action_attempt"] = 1
    new["event_counts"] = deepcopy(old["event_counts"])
    old["field_names_by_kind"]["mt5_action_attempt"] = old["field_names_by_kind"].pop("mt5_order_result")
    new["field_names_by_kind"]["mt5_action_attempt"] = new["field_names_by_kind"].pop("mt5_order_result")
    old["field_names_by_kind"]["mt5_action_attempt"].extend(["message_revision_id", "result"])
    new["field_names_by_kind"]["mt5_action_attempt"].extend(["message_revision_id", "result"])
    old["field_names_by_kind"]["mt5_position_snapshot"].append("action_revision")
    new["field_names_by_kind"]["mt5_position_snapshot"].append("action_revision")
    new["events"][0].update({"message_revision_id": "msgrev_1", "result_deal": 201})
    new["events"][1]["action_revision"] = "actionrev_1"
    with pytest.raises(ValueError, match="unexpected added field"):
        compare_projection(old, new, restore_native_details=True)
    report = compare_projection(old, new, restore_native_details=True,
                                allow_schema_expansion=True)
    assert report["schema_expansion_event_count"] == 2
    assert report["native_open_position_detail_count"] == 1
    new["events"][0]["fabricated"] = "value"
    with pytest.raises(ValueError, match="unexpected added field"):
        compare_projection(old, new, restore_native_details=True,
                           allow_schema_expansion=True)


def test_schema_expansion_rejects_derived_field_without_source_parent():
    old, new = detailed_probes()
    new["events"][0]["result_deal"] = 201
    with pytest.raises(ValueError, match="unexpected added field"):
        compare_projection(old, new, restore_native_details=True,
                           allow_schema_expansion=True)


def test_snapshot_interval_is_preserved_even_when_position_is_closed():
    old, new = detailed_probes()
    new["events"][1] = {"ev": "mt5_position_snapshot", "event_id": "snapshot",
                         "tp": 101.1, "position_exists": False}
    interval = {"positions_read_started_utc": "2026-09-18T12:00:00.001000+00:00",
                "positions_read_completed_utc": "2026-09-18T12:00:00.002000+00:00",
                "positions_read_elapsed_ms": 1.0}
    old["field_names_by_kind"]["mt5_position_snapshot"].extend(interval)
    new["field_names_by_kind"]["mt5_position_snapshot"].extend(interval)
    new["events"][1].update(interval)
    result = compare_projection(old, new, restore_native_details=True,
                                allow_schema_expansion=True)
    assert result["position_exists_false"] == 1
    new["events"][1]["positions_read_completed_utc"] = "2026-09-18T12:00:00+00:00"
    with pytest.raises(ValueError, match="read interval"):
        compare_projection(old, new, restore_native_details=True,
                           allow_schema_expansion=True)
