"""Bounded VM journal extraction never exports raw Telegram text."""

from datetime import datetime, timezone
import json

import pytest

from tools.audit_vm_order_timing_windows import build_native_week_windows, build_windows, extract


def test_entry_window_uses_receipt_and_native_fill():
    receipt = "2026-09-17T12:28:53.250+00:00"
    fill_utc = datetime.fromisoformat("2026-09-17T12:31:07.803+00:00")
    native_source_msc = int(fill_utc.timestamp() * 1000) + 10_800_000
    comparison = {"comparisons": [{"signal_id": "canal2_3086",
                                   "first_native_entry_source_msc": native_source_msc}]}
    windows = build_windows(comparison, [{"ev": "signal_received", "sig": "canal2_3086", "ts": receipt}])
    assert windows[0]["start_utc"] == "2026-09-17T12:28:38.250+00:00"
    assert windows[0]["end_utc"] == "2026-09-17T12:31:37.803+00:00"
    with pytest.raises(ValueError, match="ambiguous weekly receipt"):
        build_windows(comparison, [{"ev": "signal_received", "sig": "canal2_3086", "ts": receipt}] * 2)


def test_bounded_extraction_keeps_timing_but_not_private_text(tmp_path):
    source = tmp_path / "events.jsonl"
    rows = []
    for minute in range(10):
        stamp = f"2026-09-17T12:{minute:02d}:00.000+00:00"
        rows.append({"sig": "canal2_3086", "ev": "mt5_action_attempt", "ts": stamp,
                     "operation": "OPEN_MARKET" if minute == 4 else "MODIFY",
                     "broker_roundtrip_ns": 67_547_000_000, "raw_text": "private"})
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    report = extract({"source": str(source), "scope_start_offset": 0,
                      "scope_end_offset": source.stat().st_size, "pad_bytes": 0,
                      "windows": [{"signal_id": "canal2_3086",
                                   "start_utc": "2026-09-17T12:03:00+00:00",
                                   "end_utc": "2026-09-17T12:06:00+00:00"}]})
    events = report["windows"][0]["events"]
    assert len(events) == 1
    assert events[0]["broker_roundtrip_ns"] == 67_547_000_000
    assert "raw_text" not in json.dumps(report)
    assert report["total_scanned_bytes"] <= source.stat().st_size


def test_extraction_rejects_unbounded_windows(tmp_path):
    source = tmp_path / "events.jsonl"
    source.write_text(json.dumps({"sig": "bot", "ev": "tick", "ts": "2026-09-17T12:00:00+00:00"}) + "\n")
    with pytest.raises(ValueError, match="invalid bounded time window"):
        extract({"source": str(source), "scope_start_offset": 0,
                 "scope_end_offset": source.stat().st_size,
                 "windows": [{"signal_id": "canal2_3086",
                              "start_utc": "2026-09-17T12:00:00+00:00",
                              "end_utc": "2026-09-17T13:00:00+00:00"}]})


def test_full_week_window_caps_old_receipt_but_preserves_it():
    receipt = "2026-09-17T12:00:00+00:00"
    native_at = datetime.fromisoformat("2026-09-17T12:20:00+00:00")
    baskets = {"baskets": [{"signal_id": "canal2_1"}],
               "positions": [{"signal_id": "canal2_1",
                              "first_native_msc": int(native_at.timestamp() * 1000) + 10_800_000}]}
    windows = build_native_week_windows(baskets,
                                        [{"ev": "signal_received", "sig": "canal2_1", "ts": receipt}])
    assert windows[0]["start_utc"] == "2026-09-17T12:17:00.000+00:00"
    assert windows[0]["receipt_utc"] == "2026-09-17T12:00:00.000+00:00"
    assert windows[0]["receipt_inside_window"] is False
