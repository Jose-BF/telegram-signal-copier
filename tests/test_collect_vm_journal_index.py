import json
from types import SimpleNamespace

import pytest

from tools.audit_journal_chunk_index import scan_chunk
from tools.collect_vm_journal_index import assemble_index, capture_batch, collect_chunk


def test_assemble_requires_every_chunk_same_prefix_and_worker(tmp_path):
    source = tmp_path / "journal.jsonl"
    lines = [(json.dumps({"ts": f"2026-09-18T12:00:0{index}+00:00"}) + "\n").encode()
             for index in range(3)]
    source.write_bytes(b"".join(lines))
    chunk_bytes = len(lines[0]) + 3
    prefix_end = source.stat().st_size
    reports = [scan_chunk(source, begin, min(begin + chunk_bytes, prefix_end),
                          prefix_end=prefix_end)
               for begin in range(0, prefix_end, chunk_bytes)]
    for report in reports:
        report["local_worker_sha256"] = "a" * 64
    manifest = assemble_index(reports, source=str(source.resolve()),
                              prefix_end=prefix_end, chunk_bytes=chunk_bytes)
    assert manifest["full_prefix_bytes_indexed"] is True
    assert manifest["full_source_time_coverage_verified"] is False
    with pytest.raises(ValueError, match="incomplete"):
        assemble_index(reports[:-1], source=str(source.resolve()),
                       prefix_end=prefix_end, chunk_bytes=chunk_bytes)
    reports[1]["local_worker_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="mixed workers"):
        assemble_index(reports, source=str(source.resolve()),
                       prefix_end=prefix_end, chunk_bytes=chunk_bytes)


def test_collect_chunk_uses_bounded_read_only_worker(tmp_path):
    source = tmp_path / "journal.jsonl"
    source.write_bytes(b'{"ts":"2026-09-18T12:00:00+00:00"}\n')
    size = source.stat().st_size
    report = scan_chunk(source, 0, size, prefix_end=size)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stderr=b"", stdout=json.dumps(report).encode())

    assert collect_chunk(source=str(source.resolve()), prefix_end=size, chunk_bytes=size,
                         index=0, host="example.invalid", identity=tmp_path / "id",
                         runner=fake_run) == report
    command, kwargs = calls[0]
    assert "BatchMode=yes" in command
    assert "StrictHostKeyChecking=yes" in command
    assert "--index-worker" in command[-1]
    assert kwargs["timeout"] == 135
    report["sha256"] = "0" * 64
    report["begin"] = 1
    with pytest.raises(ValueError, match="identity"):
        collect_chunk(source=str(source.resolve()), prefix_end=size, chunk_bytes=size,
                      index=0, host="example.invalid", identity=tmp_path / "id",
                      runner=fake_run)


def test_bounded_batch_resumes_without_recollecting_or_overwriting(tmp_path):
    source = tmp_path / "journal.jsonl"
    lines = [(json.dumps({"ts": f"2026-09-18T12:00:0{index}+00:00"}) + "\n").encode()
             for index in range(3)]
    source.write_bytes(b"".join(lines))
    size, chunk_bytes = source.stat().st_size, len(lines[0])
    calls, pauses = [], []

    def fake_collect(*, source, prefix_end, chunk_bytes, index, host, identity):
        calls.append(index)
        begin = index * chunk_bytes
        return scan_chunk(source, begin, min(begin + chunk_bytes, prefix_end),
                          prefix_end=prefix_end)

    args = {"source": str(source.resolve()), "prefix_end": size,
            "chunk_bytes": chunk_bytes, "output_dir": tmp_path / "captured",
            "max_new_chunks": 2, "pause_seconds": 1,
            "host": "example.invalid", "identity": tmp_path / "id",
            "collector": fake_collect, "sleeper": pauses.append}
    first = capture_batch(**args)
    assert first["new_chunks"] == 2 and first["complete"] is False
    assert calls == [0, 1] and pauses == [1]
    second = capture_batch(**args)
    assert second["new_chunks"] == 1 and second["reused_chunks"] == 2
    assert second["complete"] is True and calls == [0, 1, 2]
    captured = tmp_path / "captured" / "chunk_0000.json"
    report = json.loads(captured.read_text(encoding="utf-8"))
    report["prefix_end"] += 1
    captured.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        capture_batch(**args)
    assert calls == [0, 1, 2]
