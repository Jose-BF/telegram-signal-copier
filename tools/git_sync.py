"""Deterministic Git recovery for the production VM watcher."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class SyncResult:
    ok: bool
    action: str
    branch: str | None
    local_head: str | None
    remote_head: str | None
    rescue_branch: str | None = None
    error: str | None = None


def _run_git(
    repo_dir: Path,
    *args: str,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=Path(repo_dir),
        capture_output=capture,
        text=True,
        check=False,
    )


def _output(repo_dir: Path, *args: str) -> str | None:
    result = _run_git(repo_dir, *args)
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip()


def _git_path(repo_dir: Path, name: str) -> Path | None:
    raw = _output(repo_dir, "rev-parse", "--git-path", name)
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else Path(repo_dir) / path


def _rebase_in_progress(repo_dir: Path) -> bool:
    return any(
        path is not None and path.exists()
        for path in (
            _git_path(repo_dir, "rebase-merge"),
            _git_path(repo_dir, "rebase-apply"),
        )
    )


def _branch(repo_dir: Path) -> str | None:
    return _output(repo_dir, "symbolic-ref", "--quiet", "--short", "HEAD")


def _head(repo_dir: Path, ref: str = "HEAD") -> str | None:
    return _output(repo_dir, "rev-parse", ref)


def _is_ancestor(repo_dir: Path, ancestor: str, descendant: str) -> bool:
    result = _run_git(
        repo_dir,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
    )
    return result.returncode == 0


def _local_commits_are_data_only(
    repo_dir: Path,
    remote_head: str,
    local_head: str,
) -> bool:
    result = _run_git(
        repo_dir,
        "log",
        "--format=%s",
        f"{remote_head}..{local_head}",
    )
    if result.returncode != 0:
        return False
    subjects = [
        line.strip()
        for line in (result.stdout or "").splitlines()
        if line.strip()
    ]
    if not subjects or not all(
        subject.startswith("data:") for subject in subjects
    ):
        return False

    commits_result = _run_git(
        repo_dir,
        "rev-list",
        "--reverse",
        f"{remote_head}..{local_head}",
    )
    if commits_result.returncode != 0:
        return False
    commits = [
        line.strip()
        for line in (commits_result.stdout or "").splitlines()
        if line.strip()
    ]
    if not commits:
        return False
    for commit in commits:
        changed = _run_git(
            repo_dir,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "--no-renames",
            "-r",
            commit,
        )
        if changed.returncode != 0:
            return False
        paths = [
            line.strip().replace("\\", "/")
            for line in (changed.stdout or "").splitlines()
            if line.strip()
        ]
        if not paths or any(
            path != "data" and not path.startswith("data/")
            for path in paths
        ):
            return False
    return True

def _rescue_branch(repo_dir: Path, head: str) -> str | None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    short = head[:8]
    base = f"vm-rescue-{stamp}-{short}"
    for suffix in range(100):
        name = base if suffix == 0 else f"{base}-{suffix}"
        result = _run_git(repo_dir, "branch", name, head)
        if result.returncode == 0:
            return name
    return None


def _failure(
    repo_dir: Path,
    action: str,
    error: str,
    *,
    rescue_branch: str | None = None,
) -> SyncResult:
    return SyncResult(
        ok=False,
        action=action,
        branch=_branch(repo_dir),
        local_head=_head(repo_dir),
        remote_head=_head(repo_dir, "origin/main"),
        rescue_branch=rescue_branch,
        error=str(error).strip(),
    )


def _attach(repo_dir: Path, branch: str, target: str) -> subprocess.CompletedProcess:
    return _run_git(repo_dir, "switch", "-C", branch, target)


def synchronize_repository(
    repo_dir: Path,
    *,
    remote: str = "origin",
    branch: str = "main",
    publish_local: bool = True,
) -> SyncResult:
    repo_dir = Path(repo_dir)
    remote_ref = f"{remote}/{branch}"
    rescue = None

    local = _head(repo_dir)
    if local is None:
        return _failure(repo_dir, "invalid_repository", "HEAD is unavailable")

    if _rebase_in_progress(repo_dir):
        rescue = _rescue_branch(repo_dir, local)
        if rescue is None:
            return _failure(
                repo_dir,
                "rebase_rescue_failed",
                "could not preserve HEAD before quitting stale rebase",
            )
        quit_result = _run_git(repo_dir, "rebase", "--quit")
        if quit_result.returncode != 0:
            return _failure(
                repo_dir,
                "rebase_quit_failed",
                quit_result.stderr or quit_result.stdout,
                rescue_branch=rescue,
            )

    fetch = _run_git(repo_dir, "fetch", remote, branch)
    if fetch.returncode != 0:
        return _failure(
            repo_dir,
            "fetch_failed",
            fetch.stderr or fetch.stdout,
            rescue_branch=rescue,
        )

    local = _head(repo_dir)
    remote_head = _head(repo_dir, remote_ref)
    if local is None or remote_head is None:
        return _failure(
            repo_dir,
            "missing_head",
            f"cannot resolve HEAD or {remote_ref}",
            rescue_branch=rescue,
        )

    worktree_status = _output(repo_dir, "status", "--porcelain")
    if worktree_status is None:
        return _failure(
            repo_dir,
            "worktree_status_failed",
            "cannot verify the working tree",
            rescue_branch=rescue,
        )
    if worktree_status:
        return _failure(
            repo_dir,
            "dirty_worktree",
            "uncommitted files must be preserved before moving Git refs",
            rescue_branch=rescue,
        )

    current_branch = _branch(repo_dir)
    if local == remote_head:
        action = "up_to_date"
        if current_branch != branch:
            attached = _attach(repo_dir, branch, local)
            if attached.returncode != 0:
                return _failure(
                    repo_dir,
                    "attach_failed",
                    attached.stderr or attached.stdout,
                    rescue_branch=rescue,
                )
            action = "attached"
    elif _is_ancestor(repo_dir, remote_head, local):
        if not _local_commits_are_data_only(repo_dir, remote_head, local):
            if rescue is None:
                rescue = _rescue_branch(repo_dir, local)
            if rescue is None:
                return _failure(
                    repo_dir,
                    "local_non_data_rescue_failed",
                    "could not preserve local non-data commits",
                )
            attached = _attach(repo_dir, branch, remote_ref)
            if attached.returncode != 0:
                return _failure(
                    repo_dir,
                    "local_non_data_attach_failed",
                    attached.stderr or attached.stdout,
                    rescue_branch=rescue,
                )
            action = "local_non_data_rescued"
        else:
            attached = _attach(repo_dir, branch, local)
            if attached.returncode != 0:
                return _failure(
                    repo_dir,
                    "attach_failed",
                    attached.stderr or attached.stdout,
                    rescue_branch=rescue,
                )
            if not publish_local:
                return _failure(
                    repo_dir,
                    "local_ahead_unpublished",
                    "local data commits require publication",
                    rescue_branch=rescue,
                )
            pushed = _run_git(repo_dir, "push", remote, f"HEAD:{branch}")
            if pushed.returncode != 0:
                return _failure(
                    repo_dir,
                    "push_failed",
                    pushed.stderr or pushed.stdout,
                    rescue_branch=rescue,
                )
            refreshed = _run_git(repo_dir, "fetch", remote, branch)
            if refreshed.returncode != 0:
                return _failure(
                    repo_dir,
                    "post_push_fetch_failed",
                    refreshed.stderr or refreshed.stdout,
                    rescue_branch=rescue,
                )
            action = (
                "attached_and_pushed"
                if current_branch != branch
                else "pushed"
            )
    elif _is_ancestor(repo_dir, local, remote_head):

        attached = _attach(repo_dir, branch, remote_ref)
        if attached.returncode != 0:
            return _failure(
                repo_dir,
                "fast_forward_failed",
                attached.stderr or attached.stdout,
                rescue_branch=rescue,
            )
        action = "fast_forwarded"
    else:
        if rescue is None:
            rescue = _rescue_branch(repo_dir, local)
        if rescue is None:
            return _failure(
                repo_dir,
                "divergence_rescue_failed",
                "could not preserve divergent local HEAD",
            )

        if (
            publish_local
            and _local_commits_are_data_only(repo_dir, remote_head, local)
        ):
            rebased = _run_git(repo_dir, "rebase", remote_ref)
            if rebased.returncode == 0:
                rebased_head = _head(repo_dir)
                if rebased_head is None:
                    return _failure(
                        repo_dir,
                        "missing_rebased_head",
                        "rebase completed without a resolvable HEAD",
                        rescue_branch=rescue,
                    )
                attached = _attach(repo_dir, branch, rebased_head)
                if attached.returncode != 0:
                    return _failure(
                        repo_dir,
                        "attach_failed",
                        attached.stderr or attached.stdout,
                        rescue_branch=rescue,
                    )
                pushed = _run_git(repo_dir, "push", remote, f"HEAD:{branch}")
                if pushed.returncode != 0:
                    return _failure(
                        repo_dir,
                        "push_failed",
                        pushed.stderr or pushed.stdout,
                        rescue_branch=rescue,
                    )
                _run_git(repo_dir, "fetch", remote, branch)
                action = "data_rebased_and_pushed"
            else:
                conflict_error = (
                    rebased.stderr
                    or rebased.stdout
                    or "data rebase conflict"
                )
                aborted = _run_git(repo_dir, "rebase", "--abort")
                if aborted.returncode != 0:
                    return _failure(
                        repo_dir,
                        "data_rebase_abort_failed",
                        aborted.stderr or aborted.stdout,
                        rescue_branch=rescue,
                    )
                return _failure(
                    repo_dir,
                    "data_rebase_conflict",
                    conflict_error,
                    rescue_branch=rescue,
                )
        else:
            attached = _attach(repo_dir, branch, remote_ref)
            if attached.returncode != 0:
                return _failure(
                    repo_dir,
                    "divergence_attach_failed",
                    attached.stderr or attached.stdout,
                    rescue_branch=rescue,
                )
            action = "diverged_rescued"

    final_branch = _branch(repo_dir)
    final_local = _head(repo_dir)
    final_remote = _head(repo_dir, remote_ref)
    if (
        final_branch != branch
        or final_local is None
        or final_local != final_remote
        or _output(repo_dir, "status", "--porcelain") != ""
        or _rebase_in_progress(repo_dir)
    ):
        return _failure(
            repo_dir,
            "verification_failed",
            "repository did not reach verified main state",
            rescue_branch=rescue,
        )
    return SyncResult(
        ok=True,
        action=action,
        branch=final_branch,
        local_head=final_local,
        remote_head=final_remote,
        rescue_branch=rescue,
    )
