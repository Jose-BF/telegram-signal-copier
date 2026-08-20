import gzip
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tools import runtime_telemetry


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


def _must_git(repo: Path, *args: str) -> str:
    result = _git(repo, *args)
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout.strip()


def _runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime_data"
    runtime.mkdir()
    (runtime / "trade_events.jsonl").write_bytes(
        b'{"ev":"one"}\n{"ev":"two"}\n'
    )
    (runtime / "trade_journal.csv").write_bytes(
        b"signal_id,status\ncanal2_1,closed\n"
    )
    return runtime


def test_default_export_includes_console_diagnostics():
    assert "bot_runtime.log" in runtime_telemetry.DEFAULT_STREAM_NAMES
    assert "telegram_media.jsonl" in runtime_telemetry.DEFAULT_STREAM_NAMES


def test_checkpoint_exports_only_complete_records_and_advances_cursor(
    tmp_path,
):
    runtime = _runtime(tmp_path)
    events = runtime / "trade_events.jsonl"
    events.write_bytes(events.read_bytes() + b'{"ev":"partial"')

    result = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
        code_commit="abc123",
        created_at="2026-07-22T20:30:00+00:00",
    )

    assert result.ok is True
    assert len(result.chunks) == 1
    chunk = result.chunks[0]
    assert chunk.start == 0
    assert chunk.end == len(b'{"ev":"one"}\n{"ev":"two"}\n')
    assert gzip.decompress(chunk.payload_path.read_bytes()) == (
        b'{"ev":"one"}\n{"ev":"two"}\n'
    )
    manifest = json.loads(chunk.manifest_path.read_text(encoding="utf-8"))
    assert manifest["stream"] == "trade_events.jsonl"
    assert manifest["byte_start"] == 0
    assert manifest["byte_end"] == chunk.end
    assert manifest["code_commit"] == "abc123"
    assert result.pending_tail_bytes == {"trade_events.jsonl": 15}

    cursor = json.loads(
        (
            runtime
            / runtime_telemetry.TELEMETRY_DIR_NAME
            / "cursors"
            / "trade_events.jsonl.json"
        ).read_text(encoding="utf-8")
    )
    assert cursor["offset"] == chunk.end


def test_checkpoint_normalizes_platform_specific_gzip_header(
    tmp_path,
    monkeypatch,
):
    runtime = _runtime(tmp_path)
    real_compress = gzip.compress

    def platform_specific_compress(*args, **kwargs):
        compressed = bytearray(real_compress(*args, **kwargs))
        compressed[9] = 3
        return bytes(compressed)

    monkeypatch.setattr(
        runtime_telemetry.gzip,
        "compress",
        platform_specific_compress,
    )

    result = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )

    assert result.ok is True
    compressed = result.chunks[0].payload_path.read_bytes()
    assert compressed[9] == 255
    assert gzip.decompress(compressed) == (
        b'{"ev":"one"}\n{"ev":"two"}\n'
    )


def test_gzip_equivalence_rejects_corrupt_transport_without_raising():
    valid = gzip.compress(b'{"ev":"one"}\n', mtime=0)
    corrupt = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff" + (b"\xff" * 32)

    assert runtime_telemetry._gzip_payloads_match(valid, corrupt) is False


def test_checkpoint_retry_is_idempotent_and_append_creates_next_range(tmp_path):
    runtime = _runtime(tmp_path)

    first = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )
    retry = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )
    assert len(first.chunks) == 1
    assert retry.chunks == ()

    with (runtime / "trade_events.jsonl").open("ab") as handle:
        handle.write(b'{"ev":"three"}\n')
    appended = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )

    assert len(appended.chunks) == 1
    assert appended.chunks[0].start == first.chunks[0].end
    assert gzip.decompress(appended.chunks[0].payload_path.read_bytes()) == (
        b'{"ev":"three"}\n'
    )


def test_checkpoint_reuses_identical_chunk_after_code_version_changes(tmp_path):
    runtime = _runtime(tmp_path)

    first = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
        code_commit="old-code",
        created_at="2026-07-22T10:00:00+00:00",
    )
    cursor = (
        runtime
        / runtime_telemetry.TELEMETRY_DIR_NAME
        / "cursors"
        / "trade_events.jsonl.json"
    )
    cursor.unlink()

    repeated = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
        code_commit="new-code",
        created_at="2026-07-22T20:00:00+00:00",
    )

    assert first.ok is True
    assert repeated.ok is True
    assert len(repeated.chunks) == 1
    assert repeated.chunks[0].sha256 == first.chunks[0].sha256


def test_overlapping_checkpoint_returns_without_moving_cursor(tmp_path):
    runtime = _runtime(tmp_path)
    lock = runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "checkpoint.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text('{"pid": 123, "created_at": "now"}\n', encoding="utf-8")

    result = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )

    assert result.ok is True
    assert result.chunks == ()
    assert not (
        runtime
        / runtime_telemetry.TELEMETRY_DIR_NAME
        / "cursors"
        / "trade_events.jsonl.json"
    ).exists()
    assert lock.exists()


def test_checkpoint_rejects_rewritten_exported_prefix(tmp_path):
    runtime = _runtime(tmp_path)
    runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )
    (runtime / "trade_events.jsonl").write_bytes(
        b'{"ev":"rewritten"}\n{"ev":"two"}\n'
    )

    result = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )

    assert result.ok is False
    assert "exported prefix changed" in result.errors[0]


def test_checkpoint_hashes_the_complete_exported_prefix(tmp_path):
    runtime = _runtime(tmp_path)
    events = runtime / "trade_events.jsonl"
    with events.open("ab") as handle:
        handle.write(
            json.dumps({"pad": "x" * 6000}).encode("utf-8") + b"\n"
        )
    first = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )
    assert first.ok is True
    payload = events.read_bytes().replace(b'"one"', b'"uno"', 1)
    events.write_bytes(payload)

    repeated = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )

    assert repeated.ok is False
    assert "exported prefix changed" in repeated.errors[0]


def test_checkpoint_rejects_stream_path_escape(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("../outside.jsonl",),
    )

    assert result.ok is False
    assert "invalid stream name" in result.errors[0]
    assert not (tmp_path / "outside.jsonl").exists()


def test_materialize_verifies_hashes_and_rebuilds_exact_stream(tmp_path):
    runtime = _runtime(tmp_path)
    runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl", "trade_journal.csv"),
        max_chunk_bytes=18,
    )
    output = tmp_path / "materialized"

    result = runtime_telemetry.materialize_chunks(
        runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox",
        output,
    )

    assert result.ok is True
    assert (output / "trade_events.jsonl").read_bytes() == (
        runtime / "trade_events.jsonl"
    ).read_bytes()
    assert (output / "trade_journal.csv").read_bytes() == (
        runtime / "trade_journal.csv"
    ).read_bytes()

    manifest = next(
        (runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox").rglob(
            "*.manifest.json"
        )
    )
    payload = manifest.with_name(
        manifest.name.removesuffix(".manifest.json") + ".jsonl.gz"
    )
    payload.write_bytes(payload.read_bytes() + b"tampered")
    failed = runtime_telemetry.materialize_chunks(
        runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox",
        tmp_path / "bad-output",
    )
    assert failed.ok is False
    assert any("compressed hash mismatch" in error for error in failed.errors)


def test_materialize_rejects_a_missing_range(tmp_path):
    runtime = _runtime(tmp_path)
    checkpoint = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
        max_chunk_bytes=15,
    )
    assert len(checkpoint.chunks) == 2
    checkpoint.chunks[0].payload_path.unlink()
    checkpoint.chunks[0].manifest_path.unlink()

    result = runtime_telemetry.materialize_chunks(
        runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox",
        tmp_path / "materialized",
    )

    assert result.ok is False
    assert any("range gap" in error for error in result.errors)


def test_materialize_selects_longest_contiguous_alternate_tail(tmp_path):
    old_runtime = tmp_path / "old-runtime"
    old_runtime.mkdir()
    (old_runtime / "trade_events.jsonl").write_bytes(b'{"ev":"one"}\n')
    old = runtime_telemetry.checkpoint_runtime(
        old_runtime,
        stream_names=("trade_events.jsonl",),
    )
    assert old.ok is True

    new_runtime = tmp_path / "new-runtime"
    new_runtime.mkdir()
    newest_payload = b'{"ev":"one"}\n{"ev":"two"}\n'
    (new_runtime / "trade_events.jsonl").write_bytes(newest_payload)
    new = runtime_telemetry.checkpoint_runtime(
        new_runtime,
        stream_names=("trade_events.jsonl",),
    )
    assert new.ok is True

    combined = tmp_path / "combined"
    for runtime in (old_runtime, new_runtime):
        source = runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox"
        for path in source.rglob("*"):
            if path.is_file():
                destination = combined / path.relative_to(source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(path.read_bytes())

    output = tmp_path / "materialized"
    result = runtime_telemetry.materialize_chunks(combined, output)

    assert result.ok is True
    assert (output / "trade_events.jsonl").read_bytes() == newest_payload


def test_contiguous_selection_is_linear_in_stream_size(monkeypatch):
    payload = b"x" * 400
    chunks = [
        (offset, offset + 1, f"hash-{offset}", payload[offset:offset + 1])
        for offset in range(len(payload))
    ]
    hashed_bytes = 0
    original_sha256 = runtime_telemetry._sha256

    def bounded_sha256(value):
        nonlocal hashed_bytes
        hashed_bytes += len(value)
        if hashed_bytes > len(payload) * 10:
            raise AssertionError("contiguous selection hashed quadratic data")
        return original_sha256(value)

    monkeypatch.setattr(runtime_telemetry, "_sha256", bounded_sha256)

    selected = runtime_telemetry._select_contiguous_payload(
        "trade_events.jsonl",
        chunks,
    )

    assert selected == payload


def test_contiguous_selection_rejects_conflicting_complete_paths():
    chunks = [
        (0, 2, "h1", b"ab"),
        (2, 4, "h2", b"cd"),
        (0, 1, "h3", b"a"),
        (1, 4, "h4", b"Xcd"),
    ]

    with pytest.raises(ValueError, match="ambiguous contiguous history"):
        runtime_telemetry._select_contiguous_payload(
            "trade_events.jsonl",
            chunks,
        )


def test_materialize_validation_failure_leaves_existing_corpus_untouched(
    tmp_path,
):
    runtime = _runtime(tmp_path)
    checkpoint = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl", "trade_journal.csv"),
    )
    journal_chunk = next(
        chunk for chunk in checkpoint.chunks
        if chunk.stream == "trade_journal.csv"
    )
    journal_chunk.payload_path.write_bytes(
        journal_chunk.payload_path.read_bytes() + b"tampered"
    )
    output = tmp_path / "materialized"
    output.mkdir()
    old_events = b'{"ev":"old-corpus"}\n'
    old_journal = b"signal_id,status\nold,closed\n"
    (output / "trade_events.jsonl").write_bytes(old_events)
    (output / "trade_journal.csv").write_bytes(old_journal)
    (output / ".runtime-store.json").write_text(
        '{"source":"old"}\n', encoding="utf-8"
    )

    result = runtime_telemetry.materialize_chunks(
        runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox",
        output,
    )

    assert result.ok is False
    assert (output / "trade_events.jsonl").read_bytes() == old_events
    assert (output / "trade_journal.csv").read_bytes() == old_journal
    assert (output / ".runtime-store.json").read_text(encoding="utf-8") == (
        '{"source":"old"}\n'
    )


def test_materialize_install_failure_rolls_back_existing_corpus(
    tmp_path,
    monkeypatch,
):
    runtime = _runtime(tmp_path)
    runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl", "trade_journal.csv"),
    )
    output = tmp_path / "materialized"
    output.mkdir()
    old_events = b'{"ev":"old-corpus"}\n'
    old_journal = b"signal_id,status\nold,closed\n"
    (output / "trade_events.jsonl").write_bytes(old_events)
    (output / "trade_journal.csv").write_bytes(old_journal)
    real_replace = runtime_telemetry.os.replace

    def fail_second_install(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            source_path.parent.name == "new"
            and destination_path.parent == output
            and destination_path.name == "trade_journal.csv"
        ):
            raise OSError("simulated install interruption")
        return real_replace(source, destination)

    monkeypatch.setattr(runtime_telemetry.os, "replace", fail_second_install)

    result = runtime_telemetry.materialize_chunks(
        runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox",
        output,
    )

    assert result.ok is False
    assert (output / "trade_events.jsonl").read_bytes() == old_events
    assert (output / "trade_journal.csv").read_bytes() == old_journal
    assert not (
        output / runtime_telemetry.runtime_paths.MATERIALIZE_MARKER_NAME
    ).exists()


def test_publish_uses_isolated_checkout_and_never_changes_source_repo(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _must_git(source, "init")
    _must_git(source, "config", "user.name", "Code Owner")
    _must_git(source, "config", "user.email", "code@example.com")
    (source / "main.py").write_text("print('safe')\n", encoding="utf-8")
    _must_git(source, "add", "main.py")
    _must_git(source, "commit", "-m", "feat: code")
    source_head = _must_git(source, "rev-parse", "HEAD")

    remote = tmp_path / "remote.git"
    remote.mkdir()
    _must_git(remote, "init", "--bare")
    _must_git(source, "remote", "add", "origin", str(remote))
    _must_git(source, "push", "origin", "HEAD:main")

    runtime = source / "runtime_data"
    runtime.mkdir()
    (runtime / "trade_events.jsonl").write_bytes(b'{"ev":"one"}\n')
    checkpoint = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
        code_commit=source_head,
    )
    assert checkpoint.ok is True

    result = runtime_telemetry.publish_outbox(
        source,
        runtime,
        remote_url=str(remote),
        checkout_dir=tmp_path / "telemetry-checkout",
    )

    assert result.ok is True
    assert result.published_files == 2
    assert _must_git(source, "rev-parse", "HEAD") == source_head
    assert _must_git(source, "status", "--porcelain") == "?? runtime_data/"
    tree = _must_git(remote, "ls-tree", "-r", "--name-only", "telemetry")
    assert ".jsonl.gz" in tree
    assert ".manifest.json" in tree
    assert not list(
        (runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox").rglob(
            "*.manifest.json"
        )
    )
    assert not list(
        (runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox").rglob(
            "*.gz"
        )
    )
    materialized = tmp_path / "analysis-runtime"
    pulled = runtime_telemetry.pull_and_materialize(
        source,
        materialized,
        checkout_dir=tmp_path / "telemetry-pull-checkout",
    )
    assert pulled.ok is True
    assert (materialized / "trade_events.jsonl").read_bytes() == (
        b'{"ev":"one"}\n'
    )
    assert (
        materialized / runtime_telemetry.runtime_paths.RUNTIME_MANIFEST_NAME
    ).is_file()


def test_publish_prefers_configured_origin_push_url(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _must_git(source, "init")
    _must_git(source, "config", "user.name", "Code Owner")
    _must_git(source, "config", "user.email", "code@example.com")
    (source / "main.py").write_text("print('safe')\n", encoding="utf-8")
    _must_git(source, "add", "main.py")
    _must_git(source, "commit", "-m", "feat: code")

    writable_remote = tmp_path / "writable.git"
    writable_remote.mkdir()
    _must_git(writable_remote, "init", "--bare")
    _must_git(source, "remote", "add", "origin", str(tmp_path / "missing.git"))
    _must_git(
        source,
        "remote",
        "set-url",
        "--push",
        "origin",
        str(writable_remote),
    )

    runtime = source / "runtime_data"
    runtime.mkdir()
    (runtime / "trade_events.jsonl").write_bytes(b'{"ev":"one"}\n')
    checkpoint = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )
    assert checkpoint.ok is True

    result = runtime_telemetry.publish_outbox(
        source,
        runtime,
        checkout_dir=tmp_path / "telemetry-checkout",
    )

    assert result.ok is True
    assert result.published_files == 2
    tree = _must_git(
        writable_remote,
        "ls-tree",
        "-r",
        "--name-only",
        "telemetry",
    )
    assert ".jsonl.gz" in tree


def test_publish_lock_prevents_overlapping_transport(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _must_git(source, "init")
    _must_git(source, "config", "user.name", "Code Owner")
    _must_git(source, "config", "user.email", "code@example.com")
    (source / "main.py").write_text("print('safe')\n", encoding="utf-8")
    _must_git(source, "add", ".")
    _must_git(source, "commit", "-m", "feat: code")
    runtime = _runtime(source)
    runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )
    lock = runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "publish.lock"
    lock.write_text('{"pid": 123, "created_at": "now"}\n', encoding="utf-8")
    checkout = tmp_path / "telemetry-checkout"

    result = runtime_telemetry.publish_outbox(
        source,
        runtime,
        remote_url=str(tmp_path / "remote.git"),
        checkout_dir=checkout,
    )

    assert result.ok is False
    assert "already running" in str(result.error)
    assert not checkout.exists()
    assert lock.exists()


def test_publish_failure_keeps_outbox_and_reports_without_raising(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _must_git(source, "init")
    _must_git(source, "config", "user.name", "Code Owner")
    _must_git(source, "config", "user.email", "code@example.com")
    (source / "main.py").write_text("print('safe')\n", encoding="utf-8")
    _must_git(source, "add", ".")
    _must_git(source, "commit", "-m", "feat: code")
    runtime = _runtime(source)
    runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )

    result = runtime_telemetry.publish_outbox(
        source,
        runtime,
        remote_url=str(tmp_path / "missing-remote.git"),
        checkout_dir=tmp_path / "telemetry-checkout",
        timeout_sec=2,
    )

    assert result.ok is False
    assert result.error
    assert list(
        (runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox").rglob(
            "*.manifest.json"
        )
    )


def test_publish_rejects_source_repository_as_checkout(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _must_git(source, "init")
    _must_git(source, "config", "user.name", "Code Owner")
    _must_git(source, "config", "user.email", "code@example.com")
    (source / "main.py").write_text("print('safe')\n", encoding="utf-8")
    _must_git(source, "add", "main.py")
    _must_git(source, "commit", "-m", "feat: code")
    original_head = _must_git(source, "rev-parse", "HEAD")
    original_branch = _must_git(source, "branch", "--show-current")
    runtime = _runtime(source)
    runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )

    result = runtime_telemetry.publish_outbox(
        source,
        runtime,
        remote_url=str(tmp_path / "remote.git"),
        checkout_dir=source,
    )

    assert result.ok is False
    assert "isolated" in str(result.error)
    assert _must_git(source, "rev-parse", "HEAD") == original_head
    assert _must_git(source, "branch", "--show-current") == original_branch


def test_second_writer_accepts_same_chunks_with_new_export_metadata(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _must_git(source, "init")
    _must_git(source, "config", "user.name", "Code Owner")
    _must_git(source, "config", "user.email", "code@example.com")
    (source / "main.py").write_text("print('safe')\n", encoding="utf-8")
    _must_git(source, "add", ".")
    _must_git(source, "commit", "-m", "feat: code")
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _must_git(remote, "init", "--bare")

    first = tmp_path / "first-runtime"
    first.mkdir()
    (first / "trade_events.jsonl").write_bytes(b'{"ev":"same"}\n')
    runtime_telemetry.checkpoint_runtime(
        first,
        stream_names=("trade_events.jsonl",),
        code_commit="old-code",
        created_at="2026-07-22T10:00:00+00:00",
    )
    initial = runtime_telemetry.publish_outbox(
        source,
        first,
        remote_url=str(remote),
        checkout_dir=tmp_path / "first-checkout",
    )
    assert initial.ok is True

    second = tmp_path / "second-runtime"
    second.mkdir()
    (second / "trade_events.jsonl").write_bytes(b'{"ev":"same"}\n')
    runtime_telemetry.checkpoint_runtime(
        second,
        stream_names=("trade_events.jsonl",),
        code_commit="new-code",
        created_at="2026-07-22T20:00:00+00:00",
    )
    repeated = runtime_telemetry.publish_outbox(
        source,
        second,
        remote_url=str(remote),
        checkout_dir=tmp_path / "second-checkout",
    )

    assert repeated.ok is True
    assert repeated.published_files == 2
    assert not list(
        (second / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox").rglob("*")
    )


def test_second_writer_accepts_equivalent_cross_python_gzip(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _must_git(source, "init")
    _must_git(source, "config", "user.name", "Code Owner")
    _must_git(source, "config", "user.email", "code@example.com")
    (source / "main.py").write_text("print('safe')\n", encoding="utf-8")
    _must_git(source, "add", ".")
    _must_git(source, "commit", "-m", "feat: code")
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _must_git(remote, "init", "--bare")

    first = tmp_path / "first-runtime"
    first.mkdir()
    (first / "trade_events.jsonl").write_bytes(b'{"ev":"same"}\n')
    runtime_telemetry.checkpoint_runtime(
        first,
        stream_names=("trade_events.jsonl",),
    )
    initial = runtime_telemetry.publish_outbox(
        source,
        first,
        remote_url=str(remote),
        checkout_dir=tmp_path / "first-checkout",
    )
    assert initial.ok is True

    second = tmp_path / "second-runtime"
    second.mkdir()
    (second / "trade_events.jsonl").write_bytes(b'{"ev":"same"}\n')
    checkpoint = runtime_telemetry.checkpoint_runtime(
        second,
        stream_names=("trade_events.jsonl",),
    )
    assert checkpoint.ok is True
    chunk = checkpoint.chunks[0]
    compressed = bytearray(chunk.payload_path.read_bytes())
    assert compressed[:3] == b"\x1f\x8b\x08"
    compressed[9] = 3 if compressed[9] != 3 else 255
    chunk.payload_path.write_bytes(compressed)
    manifest = json.loads(chunk.manifest_path.read_text(encoding="utf-8"))
    manifest["compressed_bytes"] = len(compressed)
    manifest["compressed_sha256"] = hashlib.sha256(compressed).hexdigest()
    chunk.manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    repeated = runtime_telemetry.publish_outbox(
        source,
        second,
        remote_url=str(remote),
        checkout_dir=tmp_path / "second-checkout",
    )

    assert repeated.ok is True
    assert repeated.published_files == 2
    assert not list(
        (second / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox").rglob("*")
    )


def test_pull_cli_defaults_to_ignored_runtime_store(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(runtime_telemetry, "ROOT", tmp_path)

    def fake_pull(source_repo, output_dir, **kwargs):
        captured["source"] = source_repo
        captured["output"] = output_dir
        return runtime_telemetry.MaterializeResult(True, (), ())

    monkeypatch.setattr(runtime_telemetry, "pull_and_materialize", fake_pull)

    assert runtime_telemetry.cli(["--pull"]) == 0
    assert captured["source"] == tmp_path
    assert captured["output"] == tmp_path / "runtime_data"


def test_pull_marks_materialized_output_as_active_runtime_store(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    checkout = tmp_path / "checkout"
    output = source / "runtime_data"

    def fake_git_output(cwd, *args, **kwargs):
        if args[:3] == ("remote", "get-url", "origin"):
            return "remote-url"
        if args == ("rev-parse", "HEAD"):
            return "telemetry-commit"
        raise AssertionError(args)

    monkeypatch.setattr(runtime_telemetry, "_git_output", fake_git_output)
    monkeypatch.setattr(
        runtime_telemetry,
        "_ensure_checkout",
        lambda *args, **kwargs: (True, None),
    )
    monkeypatch.setattr(
        runtime_telemetry,
        "_assemble_chunks",
        lambda chunks: ({"trade_events.jsonl": b'{"ev":"one"}\n'}, ()),
    )

    result = runtime_telemetry.pull_and_materialize(
        source,
        output,
        checkout_dir=checkout,
    )

    assert result.ok is True
    manifest = json.loads(
        (output / runtime_telemetry.runtime_paths.RUNTIME_MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert manifest["source"] == "telemetry_branch"
    assert manifest["telemetry_commit"] == "telemetry-commit"
    assert manifest["streams"]["trade_events.jsonl"]["bytes"] == 13
