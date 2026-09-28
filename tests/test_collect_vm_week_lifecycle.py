import hashlib
import json

import pytest

from tools.collect_vm_week_lifecycle import scan_slice


START = "2026-09-14T00:00:00+00:00"
END = "2026-09-19T00:00:00+00:00"


def test_frozen_lifecycle_scan_preserves_closure_and_drops_private_payload(tmp_path):
    source = tmp_path / "journal.jsonl"
    rows = [
        {"ev": "telegram_raw", "sig": "canal1_1", "ts": START, "text": "private"},
        {"ev": "signal_received", "sig": "canal1_1", "ts": START,
         "event_id": "received-1", "trigger": "sticker", "text": "private"},
        {"ev": "lifecycle_finalization_deferred", "sig": "canal1_1",
         "ts": "2026-09-14T00:01:00+00:00", "reason": "eligible_entry_intents"},
        {"ev": "signal_closed", "sig": "canal1_1",
         "ts": "2026-09-14T00:17:00+00:00", "event_id": "closed-1",
         "total_pl": -20.0, "text": "private"},
        {"ev": "signal_closed", "sig": "canal2_2", "ts": END},
    ]
    source.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    data = source.read_bytes()
    spec = {"source": str(source), "start_offset": 0, "end_offset": len(data),
            "source_slice_sha256": hashlib.sha256(data).hexdigest(),
            "start_utc": START, "end_utc": END}

    report = scan_slice(spec, sleep_seconds=0)

    assert report["source_slice_sha256"] == spec["source_slice_sha256"]
    assert report["event_count"] == 3
    assert [row["ev"] for row in report["events"]] == [
        "signal_received", "lifecycle_finalization_deferred", "signal_closed"]
    assert report["signal_ids"] == ["canal1_1"]
    assert "private" not in json.dumps(report)
    assert "total_pl" not in json.dumps(report)


def test_frozen_lifecycle_scan_rejects_changed_bytes(tmp_path):
    source = tmp_path / "journal.jsonl"
    source.write_text(json.dumps({"ev": "signal_closed", "sig": "canal1_1",
                                  "ts": START}) + "\n", encoding="utf-8")
    spec = {"source": str(source), "start_offset": 0,
            "end_offset": source.stat().st_size,
            "source_slice_sha256": "0" * 64, "start_utc": START, "end_utc": END}
    with pytest.raises(ValueError, match="hash"):
        scan_slice(spec, sleep_seconds=0)
