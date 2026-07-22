import subprocess
from pathlib import Path

from tools import runtime_recovery


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


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _must_git(repo, "init")
    _must_git(repo, "config", "user.name", "VM Bot")
    _must_git(repo, "config", "user.email", "vm@example.com")
    (repo / ".gitignore").write_text("data_backup/\n", encoding="utf-8")
    (repo / "main.py").write_text("print('safe')\n", encoding="utf-8")
    data = repo / "data"
    data.mkdir()
    (data / "trade_events.jsonl").write_text(
        '{"ev":"base"}\n', encoding="utf-8"
    )
    (data / "trade_journal.csv").write_text(
        "sig_id,status\n", encoding="utf-8"
    )
    (data / "reconcile_status.json").write_text(
        '{"ok":true}\n', encoding="utf-8"
    )
    (data / "provider_signal_catalog.json").write_text(
        '{"version":"base"}\n', encoding="utf-8"
    )
    _must_git(repo, "add", ".")
    _must_git(repo, "commit", "-m", "feat: base")
    return repo


def test_crash_recovery_checkpoints_raw_evidence_and_restores_reports(tmp_path):
    repo = _repo(tmp_path)
    data = repo / "data"
    with (data / "trade_events.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"ev":"after-crash"}\n')
    (data / "trade_journal.csv").write_text(
        "sig_id,status\ncanal2_278,closed\n", encoding="utf-8"
    )
    (data / "reconcile_status.json").write_text(
        '{"ok":false,"interrupted":true}\n', encoding="utf-8"
    )
    (data / "provider_signal_catalog.json").unlink()

    result = runtime_recovery.prepare_runtime_worktree(
        repo,
        timestamp="20260722-160500",
    )

    assert result.ok is True
    assert result.action == "checkpointed_and_repaired"
    assert result.source_paths == (
        "data/trade_events.jsonl",
        "data/trade_journal.csv",
    )
    assert set(result.restored_paths) == {
        "data/provider_signal_catalog.json",
        "data/reconcile_status.json",
    }
    assert _must_git(repo, "status", "--porcelain") == ""
    assert "after-crash" in _must_git(
        repo, "show", "HEAD:data/trade_events.jsonl"
    )
    assert _must_git(repo, "show", "HEAD:data/reconcile_status.json") == (
        '{"ok":true}'
    )
    assert _must_git(repo, "show", "--format=%s", "-s", "HEAD").startswith(
        "data: automatic recovery checkpoint"
    )
    changed = _must_git(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        "HEAD",
    )
    assert set(changed.splitlines()) == {
        "data/trade_events.jsonl",
        "data/trade_journal.csv",
    }


def test_crash_recovery_blocks_and_preserves_source_changes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "main.py").write_text("print('local edit')\n", encoding="utf-8")
    events = repo / "data" / "trade_events.jsonl"
    with events.open("a", encoding="utf-8") as f:
        f.write('{"ev":"valuable"}\n')

    result = runtime_recovery.prepare_runtime_worktree(repo)

    assert result.ok is False
    assert result.action == "unsafe_worktree"
    assert result.unsafe_paths == ("main.py",)
    assert "local edit" in (repo / "main.py").read_text(encoding="utf-8")
    assert "valuable" in events.read_text(encoding="utf-8")
    assert _must_git(repo, "log", "-1", "--format=%s") == "feat: base"


def test_crash_recovery_preserves_untracked_partial_analysis_outside_git(
    tmp_path,
):
    repo = _repo(tmp_path)
    partial = repo / "data" / "simulation_runs" / "partial" / "run_card.json"
    partial.parent.mkdir(parents=True)
    partial.write_text('{"incomplete":true}\n', encoding="utf-8")

    result = runtime_recovery.prepare_runtime_worktree(
        repo,
        timestamp="20260722-160500",
    )

    assert result.ok is True
    assert result.action == "repaired"
    assert result.archived_paths == (
        "data/simulation_runs/partial/run_card.json",
    )
    assert not partial.exists()
    archived = (
        repo
        / "data_backup"
        / "interrupted-analysis-20260722-160500"
        / "data"
        / "simulation_runs"
        / "partial"
        / "run_card.json"
    )
    assert archived.read_text(encoding="utf-8") == '{"incomplete":true}\n'
    assert _must_git(repo, "status", "--porcelain") == ""


def test_crash_recovery_never_commits_deleted_raw_evidence(tmp_path):
    repo = _repo(tmp_path)
    events = repo / "data" / "trade_events.jsonl"
    events.unlink()

    result = runtime_recovery.prepare_runtime_worktree(repo)

    assert result.ok is False
    assert result.action == "raw_evidence_deleted"
    assert result.unsafe_paths == ("data/trade_events.jsonl",)
    assert not events.exists()
    assert _must_git(repo, "log", "-1", "--format=%s") == "feat: base"


def test_crash_recovery_archives_and_truncates_partial_jsonl_tail(tmp_path):
    repo = _repo(tmp_path)
    events = repo / "data" / "trade_events.jsonl"
    with events.open("ab") as target:
        target.write(b'{"ev":"complete"}\n{"ev":"partial"')

    result = runtime_recovery.prepare_runtime_worktree(
        repo,
        timestamp="20260722-160500",
    )

    assert result.ok is True
    assert result.action == "checkpointed_and_repaired"
    assert "data/trade_events.jsonl.partial-tail" in result.archived_paths
    assert events.read_bytes().endswith(b'{"ev":"complete"}\n')
    assert b'partial' not in _must_git(
        repo, "show", "HEAD:data/trade_events.jsonl"
    ).encode()
    tail = (
        repo
        / "data_backup"
        / "interrupted-analysis-20260722-160500"
        / "data"
        / "trade_events.jsonl.partial-tail"
    )
    assert tail.read_bytes() == b'{"ev":"partial"'


def test_crash_recovery_rejects_non_append_jsonl_mutation(tmp_path):
    repo = _repo(tmp_path)
    events = repo / "data" / "trade_events.jsonl"
    events.write_text('{"ev":"rewritten"}\n', encoding="utf-8")

    result = runtime_recovery.prepare_runtime_worktree(repo)

    assert result.ok is False
    assert result.action == "raw_not_append_only"
    assert "rewritten" in events.read_text(encoding="utf-8")


def test_canonical_catalog_is_archived_before_head_is_restored(tmp_path):
    repo = _repo(tmp_path)
    catalog = repo / "data" / "provider_signal_catalog.json"
    catalog.write_text('{"version":"new-canonical"}\n', encoding="utf-8")

    result = runtime_recovery.prepare_runtime_worktree(
        repo,
        timestamp="20260722-160500",
    )

    assert result.ok is True
    assert "data/provider_signal_catalog.json" in result.archived_paths
    assert catalog.read_text(encoding="utf-8") == '{"version":"base"}\n'
    archived = (
        repo
        / "data_backup"
        / "interrupted-analysis-20260722-160500"
        / "data"
        / "provider_signal_catalog.json"
    )
    assert archived.read_text(encoding="utf-8") == (
        '{"version":"new-canonical"}\n'
    )
