import hashlib
import base64
import ctypes
import json
import subprocess
import sys

import pytest

from research.causal_replay import RAW_FIELDS as CAUSAL_RAW_FIELDS
from tools.collect_vm_raw_messages import RAW_FIELDS, collect, scan_slice, validate_spec
from tools import collect_vm_raw_messages as collector


def fixture(tmp_path):
    source = tmp_path / "journal.jsonl"
    prefix = b'{"ev":"older"}\n'
    rows = [
        {"ev": "telegram_raw", "channel": "canal2", "message_id": 3011,
         "message_revision_id": "rev-1", "date_utc": "2026-09-17T12:00:00+00:00",
         "ts": "2026-09-17T12:00:01+00:00", "text": "Buy Gold Now",
         "is_edit": False, "edit_date_utc": None, "reply_to_msg_id": None,
         "sticker_id": None, "profit": 9999, "position_id": 123},
        {"ev": "mt5_order_result", "ts": "2026-09-17T12:00:01+00:00",
         "profit": 9999},
        {"ev": "telegram_raw", "channel": "canal2", "message_id": 3012,
         "message_revision_id": "rev-2", "date_utc": "2026-09-19T00:00:00+00:00",
         "ts": "2026-09-19T00:00:00+00:00", "text": "Sell Gold Now"},
    ]
    selected = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    source.write_bytes(prefix + selected + b'{"ev":"later"}\n')
    spec = {"source": str(source), "start_offset": len(prefix),
            "end_offset": len(prefix) + len(selected),
            "source_slice_sha256": hashlib.sha256(selected).hexdigest(),
            "start_utc": "2026-09-14T00:00:00+00:00",
            "end_utc": "2026-09-19T00:00:00+00:00"}
    return spec


def test_extracts_only_raw_causal_fields_inside_exact_frozen_slice(tmp_path):
    assert RAW_FIELDS == CAUSAL_RAW_FIELDS
    report = scan_slice(fixture(tmp_path), sleep_seconds=0)
    assert report["raw_row_count"] == 1
    assert set(report["rows"][0]) == set(RAW_FIELDS)
    assert report["rows"][0]["text"] == "Buy Gold Now"
    assert "profit" not in report["rows"][0]
    assert "position_id" not in report["rows"][0]
    assert report["complete_week_claim"] is False


def test_changed_bytes_or_line_boundary_are_rejected(tmp_path):
    spec = fixture(tmp_path)
    with pytest.raises(ValueError, match="hash or boundary"):
        scan_slice({**spec, "source_slice_sha256": "0" * 64}, sleep_seconds=0)
    with pytest.raises(ValueError, match="line boundary"):
        scan_slice({**spec, "start_offset": spec["start_offset"] + 1}, sleep_seconds=0)
    with pytest.raises(ValueError, match="line boundary"):
        scan_slice({**spec, "end_offset": spec["end_offset"] - 1}, sleep_seconds=0)


def test_invalid_window_and_truncated_source_are_rejected(tmp_path):
    spec = fixture(tmp_path)
    with pytest.raises(ValueError, match="specification"):
        validate_spec({**spec, "start_utc": spec["end_utc"]})
    with pytest.raises(ValueError, match="truncated"):
        scan_slice({**spec, "end_offset": 999999}, sleep_seconds=0)


def test_transport_rejects_unbound_remote_reply_without_local_output(tmp_path, monkeypatch):
    spec = fixture(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"status": "complete", "incomplete_tail_bytes": 0,
                                    "scanned_bytes": spec["end_offset"] - spec["start_offset"],
                                    **{key: spec[key] for key in (
                                        "source", "start_offset", "end_offset",
                                        "source_slice_sha256")}}), encoding="utf-8")
    result = scan_slice(spec, sleep_seconds=0)
    output = tmp_path / "raw-week.json"

    def fake_remote(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, json.dumps({
            **result, "source_slice_sha256": "0" * 64}).encode(), b"")

    monkeypatch.setattr("tools.collect_vm_raw_messages.subprocess.run", fake_remote)
    with pytest.raises(ValueError, match="source binding"):
        collect(manifest, "vm", tmp_path / "identity", output,
                spec["start_utc"], spec["end_utc"])
    assert not output.exists()


def test_transport_saves_bound_reply_only_locally(tmp_path, monkeypatch):
    spec = fixture(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"status": "complete", "incomplete_tail_bytes": 0,
                                    "scanned_bytes": spec["end_offset"] - spec["start_offset"],
                                    **{key: spec[key] for key in (
                                        "source", "start_offset", "end_offset",
                                        "source_slice_sha256")}}), encoding="utf-8")
    result = scan_slice(spec, sleep_seconds=0)
    output = tmp_path / "raw-week.json"

    def fake_remote(*args, **kwargs):
        assert kwargs["input"].startswith(b'"""Read only raw Telegram')
        return subprocess.CompletedProcess(args[0], 0, json.dumps(result).encode(), b"")

    monkeypatch.setattr("tools.collect_vm_raw_messages.subprocess.run", fake_remote)
    saved = collect(manifest, "vm", tmp_path / "identity", output,
                    spec["start_utc"], spec["end_utc"])
    assert saved["raw_rows"] == 1
    assert json.loads(output.read_text(encoding="utf-8"))["rows"] == result["rows"]
    with pytest.raises(FileExistsError):
        collect(manifest, "vm", tmp_path / "identity", output,
                spec["start_utc"], spec["end_utc"])


def test_linux_worker_lowers_cpu_priority_before_reading(monkeypatch, capsys):
    calls = []
    spec = {"source": "/not-read-in-test"}
    encoded = base64.b64encode(json.dumps(spec).encode()).decode()
    monkeypatch.setattr(sys, "argv", ["worker", "--worker", encoded])
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(collector.os, "nice", lambda value: calls.append(("nice", value)),
                        raising=False)
    monkeypatch.setattr(collector, "scan_slice", lambda value: calls.append(("scan", value)) or {})
    collector.main()
    assert calls == [("nice", 19), ("scan", spec)]
    assert capsys.readouterr().out.strip() == "{}"


def test_windows_worker_enters_background_mode_before_reading(monkeypatch, capsys):
    calls = []
    spec = {"source": "/not-read-in-test"}
    encoded = base64.b64encode(json.dumps(spec).encode()).decode()
    monkeypatch.setattr(sys, "argv", ["worker", "--worker", encoded])
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(collector, "_enter_windows_background_mode",
                        lambda: calls.append("background"), raising=False)
    monkeypatch.setattr(collector, "scan_slice",
                        lambda value: calls.append(("scan", value)) or {})
    collector.main()
    assert calls == ["background", ("scan", spec)]
    assert capsys.readouterr().out.strip() == "{}"


def test_windows_background_mode_uses_resource_priority_and_fails_closed(monkeypatch):
    calls = []

    class Function:
        def __init__(self, result):
            self.result = result

        def __call__(self, *args):
            calls.append(args)
            return self.result

    class Kernel:
        GetCurrentProcess = Function(-1)
        SetPriorityClass = Function(1)

    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error: Kernel())
    collector._enter_windows_background_mode()
    assert calls == [(), (-1, 0x00100000)]

    Kernel.SetPriorityClass = Function(0)
    with pytest.raises(OSError, match="background resource priority"):
        collector._enter_windows_background_mode()
