import subprocess
from pathlib import Path

from tools import git_sync


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


def _write_commit(repo: Path, name: str, content: str, subject: str) -> str:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _must_git(repo, "add", name)
    _must_git(repo, "commit", "-m", subject)
    return _must_git(repo, "rev-parse", "HEAD")


def _repos(tmp_path: Path) -> tuple[Path, Path, Path]:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    vm = tmp_path / "vm"
    _must_git(tmp_path, "init", "--bare", str(remote))
    _must_git(tmp_path, "clone", str(remote), str(seed))
    _must_git(seed, "config", "user.name", "Test User")
    _must_git(seed, "config", "user.email", "test@example.com")
    _must_git(seed, "switch", "-c", "main")
    _write_commit(seed, "app.txt", "base\n", "feat: base")
    _must_git(seed, "push", "-u", "origin", "main")
    _must_git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    _must_git(tmp_path, "clone", str(remote), str(vm))
    _must_git(vm, "config", "user.name", "VM Bot")
    _must_git(vm, "config", "user.email", "vm@example.com")
    return remote, seed, vm


def test_detached_local_ahead_attaches_main_and_pushes_head(tmp_path):
    _remote, _seed, vm = _repos(tmp_path)
    local_head = _write_commit(
        vm,
        "data/events.jsonl",
        '{"event":"session"}\n',
        "data: session",
    )
    _must_git(vm, "checkout", "--detach", local_head)

    result = git_sync.synchronize_repository(vm, publish_local=True)

    assert result.ok is True
    assert result.action == "attached_and_pushed"
    assert result.branch == "main"
    assert result.local_head == local_head
    assert result.remote_head == local_head
    assert _must_git(vm, "branch", "--show-current") == "main"
    assert _must_git(vm, "rev-parse", "origin/main") == local_head


def test_remote_ahead_fast_forwards_and_attaches_main(tmp_path):
    _remote, seed, vm = _repos(tmp_path)
    old_head = _must_git(vm, "rev-parse", "HEAD")
    _must_git(vm, "checkout", "--detach", old_head)
    remote_head = _write_commit(seed, "app.txt", "updated\n", "fix: update")
    _must_git(seed, "push", "origin", "main")

    result = git_sync.synchronize_repository(vm, publish_local=True)

    assert result.ok is True
    assert result.action == "fast_forwarded"
    assert result.branch == "main"
    assert result.local_head == remote_head
    assert result.remote_head == remote_head
    assert _must_git(vm, "branch", "--show-current") == "main"


def test_non_data_divergence_preserves_rescue_and_activates_remote(tmp_path):
    _remote, seed, vm = _repos(tmp_path)
    local_head = _write_commit(vm, "local.txt", "local\n", "fix: local")
    remote_head = _write_commit(seed, "remote.txt", "remote\n", "fix: remote")
    _must_git(seed, "push", "origin", "main")

    result = git_sync.synchronize_repository(vm, publish_local=True)

    assert result.ok is True
    assert result.action == "diverged_rescued"
    assert result.rescue_branch is not None
    assert result.branch == "main"
    assert result.local_head == remote_head
    assert result.remote_head == remote_head
    assert _must_git(vm, "rev-parse", result.rescue_branch) == local_head


def test_data_only_divergence_rebases_and_pushes_after_rescue(tmp_path):
    _remote, seed, vm = _repos(tmp_path)
    local_head = _write_commit(
        vm,
        "data/events.jsonl",
        '{"event":"session"}\n',
        "data: session",
    )
    remote_code_head = _write_commit(
        seed,
        "app.txt",
        "remote code\n",
        "fix: production update",
    )
    _must_git(seed, "push", "origin", "main")

    result = git_sync.synchronize_repository(vm, publish_local=True)

    assert result.ok is True
    assert result.action == "data_rebased_and_pushed"
    assert result.rescue_branch is not None
    assert _must_git(vm, "rev-parse", result.rescue_branch) == local_head
    assert result.branch == "main"
    assert result.local_head == result.remote_head
    assert result.local_head != remote_code_head
    assert _must_git(vm, "show", "HEAD:data/events.jsonl") == (
        '{"event":"session"}'
    )
    assert _must_git(vm, "show", "HEAD:app.txt") == "remote code"

def test_stale_rebase_is_quit_after_preserving_head(tmp_path, monkeypatch):
    _remote, _seed, vm = _repos(tmp_path)
    git_dir = Path(_must_git(vm, "rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = vm / git_dir
    rebase_dir = git_dir / "rebase-merge"
    rebase_dir.mkdir(parents=True)
    (rebase_dir / "head-name").write_text("refs/heads/main\n", encoding="utf-8")
    (rebase_dir / "orig-head").write_text(
        _must_git(vm, "rev-parse", "HEAD") + "\n",
        encoding="utf-8",
    )

    real_run = git_sync._run_git

    def fake_run(repo_dir, *args, **kwargs):
        if args == ("rebase", "--quit"):
            for child in rebase_dir.iterdir():
                child.unlink()
            rebase_dir.rmdir()
            return subprocess.CompletedProcess(args=args, returncode=0,
                                               stdout="", stderr="")
        return real_run(repo_dir, *args, **kwargs)

    monkeypatch.setattr(git_sync, "_run_git", fake_run)

    result = git_sync.synchronize_repository(vm, publish_local=True)

    assert result.ok is True
    assert result.rescue_branch is not None
    assert not rebase_dir.exists()
    assert result.branch == "main"


def test_dirty_worktree_blocks_without_stashing_or_moving_refs(tmp_path):
    _remote, seed, vm = _repos(tmp_path)
    local_head = _must_git(vm, "rev-parse", "HEAD")
    live_events = vm / "data" / "live.jsonl"
    live_events.parent.mkdir(parents=True, exist_ok=True)
    live_events.write_text('{"event":"not-committed"}\n', encoding="utf-8")
    remote_head = _write_commit(seed, "app.txt", "updated\n", "fix: update")
    _must_git(seed, "push", "origin", "main")

    result = git_sync.synchronize_repository(vm, publish_local=True)

    assert result.ok is False
    assert result.action == "dirty_worktree"
    assert _must_git(vm, "rev-parse", "HEAD") == local_head
    assert _must_git(vm, "rev-parse", "origin/main") == remote_head
    assert live_events.read_text(encoding="utf-8") == (
        '{"event":"not-committed"}\n'
    )
    assert _git(vm, "rev-parse", "refs/stash").returncode != 0


def test_local_ahead_code_commit_is_rescued_but_not_pushed(tmp_path):
    _remote, _seed, vm = _repos(tmp_path)
    remote_head = _must_git(vm, "rev-parse", "origin/main")
    local_head = _write_commit(vm, "local.py", "unsafe = True\n", "fix: local")

    result = git_sync.synchronize_repository(vm, publish_local=True)

    assert result.ok is True
    assert result.action == "local_non_data_rescued"
    assert result.rescue_branch is not None
    assert _must_git(vm, "rev-parse", result.rescue_branch) == local_head
    assert _must_git(vm, "rev-parse", "HEAD") == remote_head
    assert _must_git(vm, "rev-parse", "origin/main") == remote_head


def test_data_subject_with_code_changes_is_not_auto_published(tmp_path):
    _remote, seed, vm = _repos(tmp_path)
    local_head = _write_commit(
        vm,
        "bot.py",
        "unsafe = True\n",
        "data: misleading subject",
    )
    remote_head = _write_commit(
        seed,
        "app.txt",
        "remote code\n",
        "fix: production update",
    )
    _must_git(seed, "push", "origin", "main")

    result = git_sync.synchronize_repository(vm, publish_local=True)

    assert result.ok is True
    assert result.action == "diverged_rescued"
    assert result.rescue_branch is not None
    assert _must_git(vm, "rev-parse", result.rescue_branch) == local_head
    assert _must_git(vm, "rev-parse", "HEAD") == remote_head
    assert _must_git(vm, "rev-parse", "origin/main") == remote_head

def test_data_rebase_conflict_blocks_on_preserved_local_history(tmp_path):
    _remote, seed, vm = _repos(tmp_path)
    _write_commit(
        seed,
        "data/events.jsonl",
        "base\n",
        "data: seed events",
    )
    _must_git(seed, "push", "origin", "main")
    _must_git(vm, "pull", "--ff-only", "origin", "main")
    local_head = _write_commit(
        vm,
        "data/events.jsonl",
        "local session\n",
        "data: local session",
    )
    remote_head = _write_commit(
        seed,
        "data/events.jsonl",
        "remote session\n",
        "data: remote session",
    )
    _must_git(seed, "push", "origin", "main")

    result = git_sync.synchronize_repository(vm, publish_local=True)

    assert result.ok is False
    assert result.action == "data_rebase_conflict"
    assert result.rescue_branch is not None
    assert _must_git(vm, "rev-parse", result.rescue_branch) == local_head
    assert _must_git(vm, "rev-parse", "HEAD") == local_head
    assert _must_git(vm, "rev-parse", "origin/main") == remote_head
    assert not git_sync._rebase_in_progress(vm)
    assert (vm / "data" / "events.jsonl").read_text(encoding="utf-8") == (
        "local session\n"
    )
