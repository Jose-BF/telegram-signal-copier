import gzip
import hashlib
import json
import os
import subprocess
import time
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


def _source_repo_with_bare_remote(tmp_path: Path) -> tuple[Path, Path, str]:
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
    return source, remote, source_head


def _large_jsonl_backlog(records: int = 121) -> bytes:
    return b"".join(
        json.dumps({"sequence": sequence}, separators=(",", ":")).encode()
        + b"\n"
        for sequence in range(records)
    )


def test_default_export_includes_console_diagnostics():
    assert "bot_runtime.log" in runtime_telemetry.DEFAULT_STREAM_NAMES
    assert "telegram_media.jsonl" in runtime_telemetry.DEFAULT_STREAM_NAMES
    assert "strategy_shadow_incidents.jsonl" in (
        runtime_telemetry.DEFAULT_STREAM_NAMES
    )


@pytest.mark.parametrize("blocked", [False, True])
def test_publication_recovers_only_an_abandoned_index_lock(tmp_path, monkeypatch, blocked):
    import psutil
    source, remote, source_head = _source_repo_with_bare_remote(tmp_path)
    runtime = _runtime(tmp_path)
    checkout = runtime / ".telemetry" / "publisher-repo"
    checkout.mkdir(parents=True)
    _must_git(checkout, "init")
    _must_git(checkout, "remote", "add", "origin", str(remote))
    _must_git(checkout, "config", "user.name", "Telemetry Test")
    _must_git(checkout, "config", "user.email", "telemetry@example.invalid")
    index_lock = checkout / ".git" / "index.lock"
    index_lock.write_bytes(b"retained interrupted index")
    old = time.time() - 3600
    os.utime(index_lock, (old, old))
    from types import SimpleNamespace
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: (
        [SimpleNamespace(info={"name": "git.exe"})] if blocked else []
    ))
    checkpoint = runtime_telemetry.checkpoint_runtime(runtime, stream_names=("trade_events.jsonl",))
    assert checkpoint.ok
    result = runtime_telemetry.publish_outbox(source, runtime, remote_url=str(remote))
    assert result.ok is (not blocked), result.error
    assert _must_git(source, "rev-parse", "HEAD") == source_head
    if blocked:
        assert index_lock.read_bytes() == b"retained interrupted index"
    else:
        assert not index_lock.exists()
        archives = list((checkout / ".git").glob("index.lock.recovered.*"))
        assert len(archives) == 1
        assert archives[0].read_bytes() == b"retained interrupted index"


@pytest.mark.parametrize("condition", ["fresh", "unknown_process", "probe_error", "missing_psutil", "changed"])
def test_index_lock_recovery_fails_closed(tmp_path, monkeypatch, condition):
    import psutil
    import sys
    from types import SimpleNamespace
    checkout = tmp_path / "publisher"
    git_dir = checkout / ".git"
    git_dir.mkdir(parents=True)
    lock = git_dir / "index.lock"
    lock.write_bytes(b"keep this")
    age = 0 if condition == "fresh" else 3600
    os.utime(lock, (time.time() - age, time.time() - age))

    def probe(attrs):
        if condition == "probe_error":
            raise psutil.AccessDenied(1)
        if condition == "changed":
            lock.write_bytes(b"changed by another writer")
        return [SimpleNamespace(info={"name": None})] if condition == "unknown_process" else []

    monkeypatch.setattr(psutil, "process_iter", probe)
    if condition == "missing_psutil":
        monkeypatch.setitem(sys.modules, "psutil", None)
    assert runtime_telemetry._recover_abandoned_index_lock(checkout) is not None
    assert lock.exists()
    assert not list(git_dir.glob("index.lock.recovered.*"))


def test_run_git_timeout_terminates_the_complete_process_tree(
        tmp_path, monkeypatch):
    terminated = []

    class TimedOutProcess:
        pid = 4321
        returncode = None

        def communicate(self, timeout=None):
            if not terminated:
                raise subprocess.TimeoutExpired(["git", "fetch"], timeout)
            return ("partial stdout", "partial stderr")

        def poll(self):
            return None

    monkeypatch.setattr(
        runtime_telemetry.subprocess,
        "Popen",
        lambda *args, **kwargs: TimedOutProcess(),
    )
    monkeypatch.setattr(
        runtime_telemetry,
        "terminate_process_tree",
        lambda process, timeout_sec=5.0: terminated.append(
            (process.pid, timeout_sec)
        ),
    )

    result = runtime_telemetry._run_git(
        tmp_path,
        "fetch",
        timeout_sec=0.25,
    )

    assert result.returncode == 124
    assert terminated == [(4321, 5.0)]
    assert result.stdout == "partial stdout"
    assert "timed out" in result.stderr


def test_git_add_batches_respect_the_command_length_limit():
    paths = [
        f"chunks/{'stream-name-' * 7}/{index:03d}.manifest.json"
        for index in range(5)
    ]

    batches = list(
        runtime_telemetry._git_add_path_batches(
            paths,
            maximum_files=100,
            maximum_command_chars=220,
        )
    )

    assert len(batches) > 1
    assert [path for batch in batches for path in batch] == paths
    assert all(
        len(subprocess.list2cmdline([
            "git", "-c", "core.safecrlf=false", "add", "--", *batch
        ])) <= 220
        for batch in batches
    )


def test_publication_health_reports_oldest_local_backlog(
        tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    checkpoint = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
    )
    assert checkpoint.ok is True
    pending_files = [
        path
        for path in (
            runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox"
        ).rglob("*")
        if path.is_file()
    ]
    for path in pending_files:
        os.utime(path, (100.0, 100.0))

    health = runtime_telemetry.publication_health(runtime, now=701.0)

    assert health["pending_files"] == 2
    assert health["pending_chunks"] == 1
    assert health["oldest_pending_age_s"] == pytest.approx(601.0)
    assert health["latest_error"] is None


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


def test_large_backlog_is_staged_in_bounded_batches_and_reconstructs(
    tmp_path,
    monkeypatch,
):
    source, remote, source_head = _source_repo_with_bare_remote(tmp_path)
    runtime = source / "runtime_data"
    runtime.mkdir()
    payload = _large_jsonl_backlog()
    (runtime / "trade_events.jsonl").write_bytes(payload)
    checkpoint = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
        max_chunk_bytes=1,
        code_commit=source_head,
    )
    assert checkpoint.ok is True
    assert len(checkpoint.chunks) == 121
    source_status = _must_git(source, "status", "--porcelain")
    checkout = tmp_path / "telemetry-checkout"
    real_run_git = runtime_telemetry._run_git
    add_batches = []

    def timeout_unbounded_add(cwd, *args, **kwargs):
        if Path(cwd).resolve() == checkout.resolve():
            if args == ("add", "chunks"):
                return subprocess.CompletedProcess(
                    ["git", *args],
                    124,
                    "",
                    "simulated unbounded git add timeout",
                )
            if args[:4] == ("-c", "core.safecrlf=false", "add", "--"):
                batch = args[4:]
                add_batches.append(batch)
                if (
                    len(batch) > 100
                    or len(subprocess.list2cmdline(["git", *args])) > 16_000
                ):
                    return subprocess.CompletedProcess(
                        ["git", *args],
                        124,
                        "",
                        "simulated oversized git add timeout",
                    )
        return real_run_git(cwd, *args, **kwargs)

    monkeypatch.setattr(
        runtime_telemetry,
        "_run_git",
        timeout_unbounded_add,
    )

    published_counts = []
    published_files = 0
    while runtime_telemetry._publication_outbox_files(runtime):
        result = runtime_telemetry.publish_outbox(
            source,
            runtime,
            remote_url=str(remote),
            checkout_dir=checkout,
        )
        assert result.ok is True
        published_counts.append(result.published_files)
        published_files += result.published_files
        remote_paths = set(
            _must_git(
                remote,
                "ls-tree",
                "-r",
                "--name-only",
                "telemetry",
            ).splitlines()
        )
        manifests = {
            path for path in remote_paths if path.endswith(".manifest.json")
        }
        assert len(remote_paths) == published_files == 2 * len(manifests)
        for manifest in manifests:
            parent, name = manifest.rsplit("/", 1)
            stem = name.removesuffix(".manifest.json")
            matching_payloads = {
                path
                for path in remote_paths
                if path.startswith(f"{parent}/{stem}.") and path.endswith(".gz")
            }
            assert len(matching_payloads) == 1

    assert published_counts == [100, 100, 42]
    assert len(add_batches) == 3
    assert all(0 < len(batch) <= 100 for batch in add_batches)
    assert all(
        len(subprocess.list2cmdline([
            "git", "-c", "core.safecrlf=false", "add", "--", *batch
        ])) <= 16_000
        for batch in add_batches
    )
    assert _must_git(source, "rev-parse", "HEAD") == source_head
    assert _must_git(source, "status", "--porcelain") == source_status
    assert not runtime_telemetry._publication_outbox_files(runtime)

    materialized = tmp_path / "materialized"
    pulled = runtime_telemetry.pull_and_materialize(
        source,
        materialized,
        checkout_dir=tmp_path / "pull-checkout",
    )
    assert pulled.ok is True
    assert (materialized / "trade_events.jsonl").read_bytes() == payload


def test_failed_add_batch_never_commits_pushes_or_deletes_and_retry_resumes(
    tmp_path,
    monkeypatch,
):
    source, remote, source_head = _source_repo_with_bare_remote(tmp_path)
    runtime = source / "runtime_data"
    runtime.mkdir()
    stream = runtime / "trade_events.jsonl"
    baseline_payload = b'{"sequence":"baseline"}\n'
    stream.write_bytes(baseline_payload)
    runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
        max_chunk_bytes=1,
        code_commit=source_head,
    )
    checkout = tmp_path / "telemetry-checkout"
    baseline = runtime_telemetry.publish_outbox(
        source,
        runtime,
        remote_url=str(remote),
        checkout_dir=checkout,
    )
    assert baseline.ok is True
    remote_head = _must_git(remote, "rev-parse", "telemetry")

    backlog_payload = _large_jsonl_backlog()
    with stream.open("ab") as handle:
        handle.write(backlog_payload)
    checkpoint = runtime_telemetry.checkpoint_runtime(
        runtime,
        stream_names=("trade_events.jsonl",),
        max_chunk_bytes=1,
        code_commit=source_head,
    )
    assert checkpoint.ok is True
    outbox = runtime / runtime_telemetry.TELEMETRY_DIR_NAME / "outbox"
    pending_before = {
        path.relative_to(outbox).as_posix(): path.read_bytes()
        for path in runtime_telemetry._publication_outbox_files(runtime)
    }
    source_status = _must_git(source, "status", "--porcelain")
    real_run_git = runtime_telemetry._run_git
    add_batches = []
    forbidden_calls = []

    def fail_completed_add_batch(cwd, *args, **kwargs):
        if Path(cwd).resolve() == checkout.resolve():
            if args[:4] == ("-c", "core.safecrlf=false", "add", "--"):
                add_batches.append(args[4:])
                if len(add_batches) == 1:
                    staged = real_run_git(cwd, *args, **kwargs)
                    assert staged.returncode == 0, staged.stderr or staged.stdout
                    return subprocess.CompletedProcess(
                        ["git", *args],
                        124,
                        "",
                        "simulated completed git add reported as timed out",
                    )
            elif (args and args[0] in {"commit", "push"}) or args[:3] == (
                "-c", "core.safecrlf=false", "commit",
            ):
                forbidden_calls.append(args)
        return real_run_git(cwd, *args, **kwargs)

    monkeypatch.setattr(
        runtime_telemetry,
        "_run_git",
        fail_completed_add_batch,
    )

    failed = runtime_telemetry.publish_outbox(
        source,
        runtime,
        remote_url=str(remote),
        checkout_dir=checkout,
    )

    assert failed.ok is False
    assert "simulated completed git add reported as timed out" in str(failed.error)
    assert len(add_batches) == 1
    assert len(add_batches[0]) == 100
    assert forbidden_calls == []
    assert _must_git(checkout, "rev-parse", "HEAD") == remote_head
    assert _must_git(remote, "rev-parse", "telemetry") == remote_head
    assert len(
        _must_git(checkout, "diff", "--cached", "--name-only").splitlines()
    ) == 100
    assert {
        path.relative_to(outbox).as_posix(): path.read_bytes()
        for path in runtime_telemetry._publication_outbox_files(runtime)
    } == pending_before
    assert _must_git(source, "rev-parse", "HEAD") == source_head
    assert _must_git(source, "status", "--porcelain") == source_status

    monkeypatch.setattr(runtime_telemetry, "_run_git", real_run_git)
    real_atomic_write = runtime_telemetry._atomic_write
    retry_checkout_writes = []

    def record_retry_writes(path, payload):
        if checkout.resolve() in Path(path).resolve().parents:
            retry_checkout_writes.append(Path(path))
        return real_atomic_write(path, payload)

    monkeypatch.setattr(runtime_telemetry, "_atomic_write", record_retry_writes)
    retried = runtime_telemetry.publish_outbox(
        source,
        runtime,
        remote_url=str(remote),
        checkout_dir=checkout,
    )

    assert retried.ok is True
    assert retried.published_files == 100
    assert retried.commit != remote_head
    assert _must_git(remote, "rev-parse", "telemetry") == retried.commit
    assert retry_checkout_writes == []

    monkeypatch.setattr(runtime_telemetry, "_atomic_write", real_atomic_write)
    remaining_counts = []
    while runtime_telemetry._publication_outbox_files(runtime):
        result = runtime_telemetry.publish_outbox(
            source,
            runtime,
            remote_url=str(remote),
            checkout_dir=checkout,
        )
        assert result.ok is True
        remaining_counts.append(result.published_files)
    assert remaining_counts == [100, 42]
    assert _must_git(source, "rev-parse", "HEAD") == source_head
    assert _must_git(source, "status", "--porcelain") == source_status

    materialized = tmp_path / "materialized-after-retry"
    pulled = runtime_telemetry.pull_and_materialize(
        source,
        materialized,
        checkout_dir=tmp_path / "pull-after-retry",
    )
    assert pulled.ok is True
    assert (materialized / "trade_events.jsonl").read_bytes() == (
        baseline_payload + backlog_payload
    )


def test_publish_keeps_line_ending_override_local_through_partial_commit(tmp_path):
    source, remote, source_head = _source_repo_with_bare_remote(tmp_path)
    runtime = source / "runtime_data"
    runtime.mkdir()
    (runtime / "trade_events.jsonl").write_bytes(_large_jsonl_backlog(1))
    runtime_telemetry.checkpoint_runtime(
        runtime, stream_names=("trade_events.jsonl",), code_commit=source_head,
    )
    checkout = tmp_path / "telemetry-checkout"
    assert runtime_telemetry._ensure_checkout(checkout, str(remote), "telemetry", 5)[0]
    _must_git(checkout, "config", "core.autocrlf", "true")
    _must_git(checkout, "config", "core.safecrlf", "true")
    published = runtime_telemetry.publish_outbox(
        source, runtime, remote_url=str(remote), checkout_dir=checkout,
    )
    assert published.ok is True, published.error
    assert not runtime_telemetry._publication_outbox_files(runtime)
    assert _must_git(checkout, "config", "core.autocrlf") == "true"
    assert _must_git(checkout, "config", "core.safecrlf") == "true"


@pytest.mark.parametrize("committed_then_changed", [False, True])
def test_missing_pair_member_requires_unchanged_committed_copy(tmp_path, committed_then_changed):
    source, _remote, _head = _source_repo_with_bare_remote(tmp_path)
    destination = source / "chunks" / "payload.gz"
    destination.parent.mkdir()
    destination.write_bytes(b"original payload")
    _must_git(source, "add", "chunks")
    if committed_then_changed:
        _must_git(source, "commit", "-m", "test: confirmed copy")
        destination.write_bytes(b"modified after commit")
    before = destination.read_bytes()
    with pytest.raises(ValueError, match="no committed copy"):
        runtime_telemetry._committed_checkout_payload(
            source, destination, timeout_sec=5,
        )
    assert destination.read_bytes() == before


@pytest.mark.parametrize("remove_manifest_first", [False, True])
def test_interrupted_confirmed_cleanup_recovers_both_orphan_kinds(
    tmp_path, monkeypatch, remove_manifest_first,
):
    source, remote, source_head = _source_repo_with_bare_remote(tmp_path)
    runtime = source / "runtime_data"
    runtime.mkdir()
    payload = _large_jsonl_backlog(2)
    (runtime / "trade_events.jsonl").write_bytes(payload)
    runtime_telemetry.checkpoint_runtime(
        runtime, stream_names=("trade_events.jsonl",), max_chunk_bytes=1,
        code_commit=source_head,
    )
    checkout = tmp_path / "telemetry-checkout"
    original_remove = runtime_telemetry._remove_confirmed_outbox_files
    assert runtime_telemetry._ensure_checkout(checkout, str(remote), "telemetry", 5)[0]
    _must_git(checkout, "config", "core.autocrlf", "true")
    _must_git(checkout, "config", "core.safecrlf", "true")

    def interrupt_cleanup(files, outbox):
        first = next(path for path in files
                     if path.name.endswith(".manifest.json") == remove_manifest_first)
        first.unlink()
        raise OSError("simulated crash during confirmed cleanup")

    monkeypatch.setattr(runtime_telemetry, "_remove_confirmed_outbox_files", interrupt_cleanup)
    failed = runtime_telemetry.publish_outbox(
        source, runtime, remote_url=str(remote), checkout_dir=checkout,
    )
    assert failed.ok is False
    confirmed_head = _must_git(remote, "rev-parse", "telemetry")
    assert len(runtime_telemetry._publication_outbox_files(runtime)) == 3

    monkeypatch.setattr(runtime_telemetry, "_remove_confirmed_outbox_files", original_remove)
    retried = runtime_telemetry.publish_outbox(
        source, runtime, remote_url=str(remote), checkout_dir=checkout,
    )
    assert retried.ok is True, retried.error
    assert retried.commit == confirmed_head
    assert not runtime_telemetry._publication_outbox_files(runtime)
    materialized = tmp_path / "materialized-orphans"
    assert runtime_telemetry.pull_and_materialize(
        source, materialized, checkout_dir=tmp_path / "pull-orphans",
    ).ok
    assert (materialized / "trade_events.jsonl").read_bytes() == payload


def test_next_incomplete_pair_does_not_block_current_complete_batch(tmp_path):
    source, remote, source_head = _source_repo_with_bare_remote(tmp_path)
    runtime = source / "runtime_data"
    runtime.mkdir()
    (runtime / "trade_events.jsonl").write_bytes(_large_jsonl_backlog(51))
    checkpoint = runtime_telemetry.checkpoint_runtime(
        runtime, stream_names=("trade_events.jsonl",), max_chunk_bytes=1,
        code_commit=source_head,
    )
    checkpoint.chunks[-1].payload_path.unlink()
    checkout = tmp_path / "telemetry-checkout"
    first = runtime_telemetry.publish_outbox(
        source, runtime, remote_url=str(remote), checkout_dir=checkout,
    )
    assert first.ok is True, first.error
    assert first.published_files == 100
    second = runtime_telemetry.publish_outbox(
        source, runtime, remote_url=str(remote), checkout_dir=checkout,
    )
    assert second.ok is False
    assert len(runtime_telemetry._publication_outbox_files(runtime)) == 1
    assert checkpoint.chunks[-1].manifest_path.is_file()
    assert _must_git(remote, "rev-parse", "telemetry") == first.commit


def test_changed_selection_does_not_commit_partial_previous_index(tmp_path, monkeypatch):
    source, remote, source_head = _source_repo_with_bare_remote(tmp_path)
    runtime = source / "runtime_data"
    runtime.mkdir()
    old_payload = _large_jsonl_backlog(50)
    (runtime / "trade_events.jsonl").write_bytes(old_payload)
    runtime_telemetry.checkpoint_runtime(
        runtime, stream_names=("trade_events.jsonl",), max_chunk_bytes=1,
        code_commit=source_head,
    )
    checkout = tmp_path / "telemetry-checkout"
    real_batches = runtime_telemetry._git_add_path_batches
    real_git = runtime_telemetry._run_git
    calls = []

    def small_batches(paths):
        return real_batches(paths, maximum_files=49)

    def fail_second_batch(cwd, *args, **kwargs):
        if Path(cwd).resolve() == checkout.resolve() and args[:4] == (
            "-c", "core.safecrlf=false", "add", "--",
        ):
            calls.append(args[4:])
            if len(calls) == 2:
                return subprocess.CompletedProcess(["git", *args], 124, "", "second batch timeout")
        return real_git(cwd, *args, **kwargs)

    monkeypatch.setattr(runtime_telemetry, "_git_add_path_batches", small_batches)
    monkeypatch.setattr(runtime_telemetry, "_run_git", fail_second_batch)
    failed = runtime_telemetry.publish_outbox(
        source, runtime, remote_url=str(remote), checkout_dir=checkout,
    )
    assert failed.ok is False
    staged_before = set(_must_git(checkout, "diff", "--cached", "--name-only").splitlines())
    assert len(staged_before) == 49

    new_payload = b"new console line\n" * 50
    (runtime / "bot_runtime.log").write_bytes(new_payload)
    runtime_telemetry.checkpoint_runtime(
        runtime, stream_names=("bot_runtime.log",), max_chunk_bytes=1,
        code_commit=source_head,
    )
    monkeypatch.setattr(runtime_telemetry, "_run_git", real_git)
    monkeypatch.setattr(runtime_telemetry, "_git_add_path_batches", real_batches)
    retried = runtime_telemetry.publish_outbox(
        source, runtime, remote_url=str(remote), checkout_dir=checkout,
    )
    assert retried.ok is True, retried.error
    published_paths = set(_must_git(remote, "ls-tree", "-r", "--name-only", "telemetry").splitlines())
    assert len(published_paths) == retried.published_files == 100
    assert all(path.startswith("chunks/bot_runtime.log/") for path in published_paths)
    assert set(_must_git(checkout, "diff", "--cached", "--name-only").splitlines()) == staged_before

    final = runtime_telemetry.publish_outbox(
        source, runtime, remote_url=str(remote), checkout_dir=checkout,
    )
    assert final.ok is True, final.error
    assert final.published_files == 100
    assert not runtime_telemetry._publication_outbox_files(runtime)
    materialized = tmp_path / "materialized-changed-selection"
    assert runtime_telemetry.pull_and_materialize(
        source, materialized, checkout_dir=tmp_path / "pull-changed-selection",
    ).ok
    assert (materialized / "trade_events.jsonl").read_bytes() == old_payload
    assert (materialized / "bot_runtime.log").read_bytes() == new_payload


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
