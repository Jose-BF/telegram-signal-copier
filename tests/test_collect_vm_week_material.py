import json
import sys

import pytest

from tools.audit_journal_chunk_index import scan_chunk
from tools.collect_vm_journal_index import WORKER as INDEX_WORKER, assemble_index
import tools.collect_vm_week_material as collector
from tools.collect_vm_week_material import (aggregate_indexed_segment, build_segments,
                                            validate_segment_result)
from tools.probe_vm_signal_lifecycle import extract_material


def test_merge_preserves_all_active_signals_without_repeated_time_scans():
    rows = []
    for index in range(40):
        start, end = ("12:00:00", "12:04:00")
        if index == 38:
            start, end = "12:03:00", "12:09:00"
        if index == 39:
            start, end = "12:15:00", "12:16:00"
        rows.append({"signal_id": f"canal2_{index}", "segments": [{
            "start_utc": f"2026-09-18T{start}+00:00",
            "end_utc": f"2026-09-18T{end}+00:00"}]})
    plan = {"contract": "week_native_lifecycle_window_plan_v1", "native_basket_count": 40,
            "rows": rows}
    segments = build_segments(plan)
    assert len(segments) == 3
    assert len(segments[0]["active_signals"]) == 39
    assert segments[1]["active_signals"] == ["canal2_38"]
    assert segments[2]["active_signals"] == ["canal2_39"]
    assert segments[0]["end_utc"] == segments[1]["start_utc"]
    assert segments[1]["end_utc"] < segments[2]["start_utc"]


def test_merge_rejects_duplicate_signal_identity():
    rows = [{"signal_id": "canal2_1", "segments": [{
        "start_utc": "2026-09-18T12:00:00+00:00",
        "end_utc": "2026-09-18T12:01:00+00:00"}]} for _ in range(40)]
    with pytest.raises(ValueError, match="identity mismatch"):
        build_segments({"contract": "week_native_lifecycle_window_plan_v1",
                        "native_basket_count": 40, "rows": rows})


def test_collector_rejects_incomplete_management_records():
    segment = {"start_utc": "2026-09-18T12:00:00+00:00",
               "end_utc": "2026-09-18T12:05:00+00:00", "active_signals": ["canal2_1"]}
    event = {"sig": "canal2_1", "ts": "2026-09-18T12:01:00+00:00", "event_id": "one"}
    result = {"contract": "bounded_vm_week_material_segment_v2", "segment": segment,
              "material_records_complete_within_segment": True,
              "management_records_complete_within_segment": True,
              "material_event_counts": [], "events": [],
              "management_event_counts": [{"count": 1}], "management_events": [event]}
    validate_segment_result(result, segment)
    result["management_event_counts"][0]["count"] = 2
    with pytest.raises(ValueError, match="management segment contract"):
        validate_segment_result(result, segment)
    result["management_event_counts"][0]["count"] = 1
    result["management_events"].append(event)
    result["management_event_counts"][0]["count"] = 2
    with pytest.raises(ValueError, match="identity"):
        validate_segment_result(result, segment)


def test_indexed_aggregate_requires_every_relevant_hashed_chunk(tmp_path):
    segment = {"start_utc": "2026-09-18T12:00:00+00:00",
               "end_utc": "2026-09-18T12:05:00+00:00", "active_signals": ["canal2_1"]}
    rows = [
        {"ts": "2026-09-18T12:00:00+00:00", "sig": "canal2_1", "ev": "signal_received", "event_id": "a"},
        {"ts": "2026-09-18T14:00:00+00:00", "sig": "canal2_1", "ev": "outside", "event_id": "outside"},
        {"ts": "2026-09-18T12:04:00+00:00", "sig": "canal2_1", "ev": "mt5_order_result", "event_id": "b"},
    ]
    lines = [(json.dumps(row) + "\n").encode() for row in rows]
    source = tmp_path / "journal.jsonl"
    source.write_bytes(b"".join(lines))
    boundary = len(lines[0]) + len(lines[1])
    index = [scan_chunk(source, begin, end, prefix_end=source.stat().st_size)
             for begin, end in ((0, boundary), (boundary, source.stat().st_size))]
    chunk_reports = [extract_material({"source": str(source), "segment": segment,
                     "scope_start_offset": chunk["actual_start"],
                     "scope_end_offset": chunk["actual_end"],
                     "indexed_chunk_sha256": chunk["sha256"]}) for chunk in index]
    merged = aggregate_indexed_segment(segment, index, chunk_reports)
    validate_segment_result(merged, segment)
    assert merged["contract"] == "bounded_vm_week_material_segment_v3"
    assert merged["indexed_prefix_time_coverage_verified"] is True
    assert merged["full_source_time_coverage_verified"] is False
    assert [row["event_id"] for row in merged["events"]] == ["a", "b"]
    with pytest.raises(ValueError, match="missing or extraneous"):
        aggregate_indexed_segment(segment, index, chunk_reports[:1])
    chunk_reports[1]["source_window_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="chunk proof"):
        aggregate_indexed_segment(segment, index, chunk_reports)


def test_indexed_cli_collects_all_selected_chunks_before_writing(tmp_path, monkeypatch):
    source = tmp_path / "journal.jsonl"
    rows = [
        {"ts": "2026-09-18T12:00:00+00:00", "sig": "canal2_0", "ev": "signal_received", "event_id": "a"},
        {"ts": "2026-09-18T14:00:00+00:00", "sig": "canal2_0", "ev": "outside", "event_id": "outside"},
        {"ts": "2026-09-18T12:04:00+00:00", "sig": "canal2_0", "ev": "mt5_order_result", "event_id": "b"},
    ]
    lines = [(json.dumps(row) + "\n").encode() for row in rows]
    source.write_bytes(b"".join(lines))
    prefix_end = source.stat().st_size
    chunk_bytes = len(lines[0]) + len(lines[1])
    chunks = [scan_chunk(source, begin, min(begin + chunk_bytes, prefix_end), prefix_end=prefix_end)
              for begin in range(0, prefix_end, chunk_bytes)]
    for chunk in chunks:
        chunk["local_worker_sha256"] = collector.digest(INDEX_WORKER)
    manifest = assemble_index(chunks, source=str(source.resolve()),
                              prefix_end=prefix_end, chunk_bytes=chunk_bytes)
    manifest_path = tmp_path / "index.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    plan = {"contract": "week_native_lifecycle_window_plan_v1", "native_basket_count": 40,
            "rows": [{"signal_id": f"canal2_{index}", "segments": [{
                "start_utc": "2026-09-18T12:00:00+00:00",
                "end_utc": "2026-09-18T12:05:00+00:00"}]} for index in range(40)]}
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    output = tmp_path / "segment.json"
    calls = []

    def fake_remote(spec, host, identity):
        calls.append(spec["scope_start_offset"])
        return extract_material(spec)

    monkeypatch.setattr(collector, "run_remote_material", fake_remote)
    monkeypatch.setattr(sys, "argv", ["collector", "--plan", str(plan_path),
                         "--segment-index", "0", "--remote-source", str(source.resolve()),
                         "--host", "example.invalid", "--identity", str(tmp_path / "id"),
                         "--index-manifest", str(manifest_path), "--output", str(output)])
    collector.main()
    result = json.loads(output.read_text(encoding="utf-8"))
    assert calls == [chunk["actual_start"] for chunk in chunks]
    assert [row["event_id"] for row in result["events"]] == ["a", "b"]
    assert result["indexed_prefix_time_coverage_verified"] is True
    assert result["full_source_time_coverage_verified"] is False
