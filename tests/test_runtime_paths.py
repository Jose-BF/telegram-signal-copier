import hashlib
import json
import tracemalloc
from pathlib import Path

import pytest

import runtime_paths


def _write_legacy(repo: Path) -> Path:
    data = repo / "data"
    data.mkdir(parents=True)
    (data / "trade_events.jsonl").write_text(
        '{"ev":"one"}\n{"ev":"two"}\n',
        encoding="utf-8",
    )
    (data / "trade_journal.csv").write_text(
        "signal_id,status\ncanal2_1,closed\n",
        encoding="utf-8",
    )
    (data / "trade_events_TEST.jsonl").write_text(
        '{"ev":"test"}\n',
        encoding="utf-8",
    )
    (data / "trade_journal_TEST.csv").write_text(
        "signal_id,status\ncanal_test_1,closed\n",
        encoding="utf-8",
    )
    (data / "strategy_shadow_incidents.jsonl").write_text(
        '{"ev":"strategy_shadow_parity_incident"}\n',
        encoding="utf-8",
    )
    return data


def test_uninitialized_checkout_reads_historical_seed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    legacy = _write_legacy(repo)
    monkeypatch.delenv("BOT_RUNTIME_DATA_DIR", raising=False)

    assert runtime_paths.active_data_dir(repo) == legacy
    assert runtime_paths.data_path("trade_events.jsonl", repo=repo) == (
        legacy / "trade_events.jsonl"
    )


def test_initialize_runtime_store_copies_and_hashes_authoritative_streams(
    tmp_path,
    monkeypatch,
):
    repo = tmp_path / "repo"
    legacy = _write_legacy(repo)
    runtime = repo / "runtime_data"
    monkeypatch.delenv("BOT_RUNTIME_DATA_DIR", raising=False)

    result = runtime_paths.initialize_runtime_store(
        repo,
        runtime_dir=runtime,
        initialized_at="2026-07-22T20:00:00+00:00",
        code_commit="abc123",
    )

    assert result.ok is True
    assert result.runtime_dir == runtime
    assert set(result.copied) == set(runtime_paths.AUTHORITATIVE_STREAMS)
    assert result.preserved == ()
    for name in runtime_paths.AUTHORITATIVE_STREAMS:
        assert (runtime / name).read_bytes() == (legacy / name).read_bytes()

    manifest = json.loads(
        (runtime / runtime_paths.RUNTIME_MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
    )
    expected = (legacy / "trade_events.jsonl").read_bytes()
    assert manifest["schema_version"] == 1
    assert manifest["initialized_at"] == "2026-07-22T20:00:00+00:00"
    assert manifest["code_commit"] == "abc123"
    assert manifest["streams"]["trade_events.jsonl"] == {
        "action": "copied",
        "bytes": len(expected),
        "sha256": hashlib.sha256(expected).hexdigest(),
        "source": "data/trade_events.jsonl",
    }
    assert runtime_paths.active_data_dir(repo) == runtime


def test_initialize_never_overwrites_existing_runtime_evidence(
    tmp_path,
    monkeypatch,
):
    repo = tmp_path / "repo"
    _write_legacy(repo)
    runtime = repo / "runtime_data"
    runtime.mkdir()
    existing = b'{"ev":"runtime-newer"}\n'
    (runtime / "trade_events.jsonl").write_bytes(existing)
    monkeypatch.delenv("BOT_RUNTIME_DATA_DIR", raising=False)

    result = runtime_paths.initialize_runtime_store(repo, runtime_dir=runtime)

    assert (runtime / "trade_events.jsonl").read_bytes() == existing
    assert "trade_events.jsonl" in result.preserved
    assert "trade_events.jsonl" not in result.copied


def test_initialize_repairs_partial_tails_in_existing_runtime_store(
    tmp_path,
    monkeypatch,
):
    repo = tmp_path / "repo"
    _write_legacy(repo)
    runtime = repo / "runtime_data"
    runtime.mkdir()
    (runtime / "trade_events.jsonl").write_bytes(
        b'{"ev":"complete"}\n{"ev":"partial"'
    )
    (runtime / "trade_journal.csv").write_bytes(
        b"signal_id,status\ncanal2_1,closed\npartial"
    )
    monkeypatch.delenv("BOT_RUNTIME_DATA_DIR", raising=False)

    result = runtime_paths.initialize_runtime_store(repo, runtime_dir=runtime)

    assert result.ok is True
    assert (runtime / "trade_events.jsonl").read_bytes() == (
        b'{"ev":"complete"}\n'
    )
    assert (runtime / "trade_journal.csv").read_bytes() == (
        b"signal_id,status\ncanal2_1,closed\n"
    )
    assert set(result.archived_tails) == {
        "recovery/trade_events.jsonl.partial-tail",
        "recovery/trade_journal.csv.partial-tail",
    }


def test_initialize_archives_partial_jsonl_tail_and_copies_complete_prefix(
    tmp_path,
    monkeypatch,
):
    repo = tmp_path / "repo"
    legacy = _write_legacy(repo)
    legacy_events = legacy / "trade_events.jsonl"
    legacy_events.write_bytes(
        b'{"ev":"complete"}\n{"ev":"interrupted"'
    )
    runtime = repo / "runtime_data"
    monkeypatch.delenv("BOT_RUNTIME_DATA_DIR", raising=False)

    result = runtime_paths.initialize_runtime_store(
        repo,
        runtime_dir=runtime,
        initialized_at="2026-07-22T20:00:00+00:00",
    )

    assert result.ok is True
    assert (runtime / "trade_events.jsonl").read_bytes() == (
        b'{"ev":"complete"}\n'
    )
    assert result.archived_tails == (
        "recovery/trade_events.jsonl.partial-tail",
    )
    assert (
        runtime / "recovery" / "trade_events.jsonl.partial-tail"
    ).read_bytes() == b'{"ev":"interrupted"'


def test_explicit_runtime_directory_is_always_authoritative(
    tmp_path,
    monkeypatch,
):
    repo = tmp_path / "repo"
    _write_legacy(repo)
    external = tmp_path / "external-runtime"
    monkeypatch.setenv("BOT_RUNTIME_DATA_DIR", str(external))

    assert runtime_paths.active_data_dir(repo) == external.resolve()
    assert runtime_paths.data_path("trade_events.jsonl", repo=repo) == (
        external.resolve() / "trade_events.jsonl"
    )


def test_interrupted_materialization_is_not_selected_for_analysis(
    tmp_path,
    monkeypatch,
):
    repo = tmp_path / "repo"
    legacy = _write_legacy(repo)
    runtime = repo / "runtime_data"
    runtime.mkdir()
    (runtime / runtime_paths.RUNTIME_MANIFEST_NAME).write_text(
        '{"source":"telemetry"}\n', encoding="utf-8"
    )
    (runtime / runtime_paths.MATERIALIZE_MARKER_NAME).write_text(
        '{"transaction_dir":"pending"}\n', encoding="utf-8"
    )
    monkeypatch.delenv("BOT_RUNTIME_DATA_DIR", raising=False)

    assert runtime_paths.active_data_dir(repo) == legacy


def test_data_path_rejects_directory_escape(tmp_path):
    repo = tmp_path / "repo"
    _write_legacy(repo)

    with pytest.raises(ValueError, match="simple filename"):
        runtime_paths.data_path("../outside.jsonl", repo=repo)


@pytest.mark.parametrize("existing", [False, True])
def test_large_stream_initialization_has_bounded_memory(tmp_path, existing):
    repo = tmp_path / "repo"
    folder = repo / ("runtime_data" if existing else "data")
    folder.mkdir(parents=True)
    source = folder / "trade_events.jsonl"
    record = json.dumps({"ev": "decision", "input": "x" * 1000}).encode() + b"\n"
    digest = hashlib.sha256()
    with source.open("wb") as stream:
        for _ in range(16384):
            stream.write(record)
            digest.update(record)
    tracemalloc.start()
    try:
        result = runtime_paths.initialize_runtime_store(repo, runtime_dir=repo / "runtime_data")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["streams"][source.name]["sha256"] == digest.hexdigest()
    assert manifest["streams"][source.name]["bytes"] == len(record) * 16384


@pytest.mark.parametrize("name,payload", [
    ("trade_events.jsonl", b'{"ev":"valid"}\ninvalid\npartial'),
    ("trade_journal.csv", b'a,b\n1,2,3\npartial'),
    ("trade_journal.csv", b'a,b\n1,"unclosed\n'),
])
def test_invalid_runtime_stream_is_preserved_without_manifest(tmp_path, name, payload):
    runtime = tmp_path / "runtime_data"
    runtime.mkdir()
    source = runtime / name
    source.write_bytes(payload)
    with pytest.raises(ValueError):
        runtime_paths.initialize_runtime_store(tmp_path, runtime_dir=runtime)
    assert source.read_bytes() == payload
    assert not (runtime / runtime_paths.RUNTIME_MANIFEST_NAME).exists()


def test_streamed_csv_preserves_bom_crlf_and_multiline_fields(tmp_path):
    source = tmp_path / "data" / "trade_journal.csv"
    source.parent.mkdir()
    payload = b'\xef\xbb\xbfa,b\r\n1,"two\r\nlines"\r\n'
    source.write_bytes(payload + b'partial')
    result = runtime_paths.initialize_runtime_store(tmp_path)
    assert (result.runtime_dir / source.name).read_bytes() == payload
    assert (result.runtime_dir / "recovery" / (source.name + ".partial-tail")).read_bytes() == b'partial'


def test_tail_repair_failure_preserves_original_and_archived_tail(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime_data"
    runtime.mkdir()
    source = runtime / "trade_events.jsonl"
    original = b'{"ev":"valid"}\npartial'
    source.write_bytes(original)
    replace = runtime_paths.os.replace

    def fail_runtime_replace(src, dst):
        if Path(dst) == source:
            raise OSError("replace failed")
        return replace(src, dst)

    monkeypatch.setattr(runtime_paths.os, "replace", fail_runtime_replace)
    with pytest.raises(OSError, match="replace failed"):
        runtime_paths.initialize_runtime_store(tmp_path)
    assert source.read_bytes() == original
    assert (runtime / "recovery" / "trade_events.jsonl.partial-tail").read_bytes() == b'partial'
    assert not list(runtime.glob("*.tmp"))


def test_changed_source_is_not_published_after_validation(tmp_path, monkeypatch):
    source = tmp_path / "data" / "trade_events.jsonl"
    source.parent.mkdir()
    source.write_bytes(b'{"ev":"first"}\n')
    inspect = runtime_paths._inspect_stream_prefix

    def inspect_then_append(path):
        result = inspect(path)
        with path.open("ab") as handle:
            handle.write(b'{"ev":"arrived"}\n')
        return result

    monkeypatch.setattr(runtime_paths, "_inspect_stream_prefix", inspect_then_append)
    with pytest.raises(ValueError, match="changed during validation"):
        runtime_paths.initialize_runtime_store(tmp_path)
    assert not (tmp_path / "runtime_data" / source.name).exists()
    assert source.read_bytes().endswith(b'{"ev":"arrived"}\n')
