import base64
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.audit_journal_chunk_index import chunks_for_window, scan_chunk


def test_chunk_index_finds_out_of_order_events_across_byte_ranges(tmp_path):
    lines = [json.dumps({"ts": stamp}).encode() + b"\n" for stamp in (
        "2026-09-18T12:00:00+00:00", "2026-09-18T14:00:00+00:00",
        "2026-09-18T12:05:00+00:00", "2026-09-18T14:05:00+00:00")]
    source = tmp_path / "journal.jsonl"
    source.write_bytes(b"".join(lines))
    boundary = len(lines[0]) + len(lines[1])
    first = scan_chunk(source, 0, boundary, prefix_end=source.stat().st_size)
    second = scan_chunk(source, boundary, source.stat().st_size,
                        prefix_end=source.stat().st_size)
    selected = chunks_for_window([second, first], "2026-09-18T12:00:00+00:00",
                                 "2026-09-18T12:10:00+00:00")
    assert [row["begin"] for row in selected] == [0, boundary]
    assert first["last_utc"] > second["first_utc"]
    assert first["actual_end"] == second["actual_start"]
    assert first["event_count"] + second["event_count"] == 4
    with pytest.raises(ValueError, match="incomplete"):
        chunks_for_window([first], "2026-09-18T12:00:00+00:00",
                          "2026-09-18T12:10:00+00:00")
    with pytest.raises(ValueError, match="start at zero"):
        chunks_for_window([second], "2026-09-18T12:00:00+00:00",
                          "2026-09-18T12:10:00+00:00")
    with pytest.raises(ValueError, match="outside indexed time span"):
        chunks_for_window([first, second], "2026-09-18T14:10:00+00:00",
                          "2026-09-18T14:20:00+00:00")
    with pytest.raises(ValueError, match="outside indexed time span"):
        chunks_for_window([first, second], "2026-09-18T11:50:00+00:00",
                          "2026-09-18T12:10:00+00:00")


def test_chunk_index_rejects_incomplete_tail_and_joins_midline_boundaries(tmp_path):
    lines = [json.dumps({"ts": f"2026-09-18T12:00:0{index}+00:00"}).encode() + b"\n"
             for index in range(3)]
    source = tmp_path / "journal.jsonl"
    source.write_bytes(b"".join(lines))
    boundary = len(lines[0]) + 5
    first = scan_chunk(source, 0, boundary, prefix_end=source.stat().st_size)
    second = scan_chunk(source, boundary, source.stat().st_size,
                        prefix_end=source.stat().st_size)
    assert first["actual_end"] == second["actual_start"] == len(lines[0]) + len(lines[1])
    assert first["event_count"] == 2
    assert second["event_count"] == 1
    source.write_bytes(source.read_bytes() + b"partial")
    with pytest.raises(ValueError, match="incomplete line"):
        scan_chunk(source, 0, source.stat().st_size, prefix_end=source.stat().st_size)


def test_index_worker_runs_when_streamed_without_project_imports(tmp_path):
    source = tmp_path / "journal.jsonl"
    source.write_bytes(b'{"ts":"2026-09-18T12:00:00+00:00"}\n')
    worker = Path(__file__).resolve().parents[1] / "tools" / "audit_journal_chunk_index.py"
    spec = {"source": str(source), "begin": 0, "end": source.stat().st_size,
            "prefix_end": source.stat().st_size}
    encoded = base64.b64encode(json.dumps(spec).encode()).decode("ascii")
    run = subprocess.run([sys.executable, "-", "--index-worker", encoded],
                         input=worker.read_bytes(), capture_output=True, check=True,
                         cwd=tmp_path, timeout=10)
    report = json.loads(run.stdout)
    assert report["sha256"] == scan_chunk(source, 0, source.stat().st_size,
                                          prefix_end=source.stat().st_size)["sha256"]
