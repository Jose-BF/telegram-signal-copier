import hashlib
import json
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
