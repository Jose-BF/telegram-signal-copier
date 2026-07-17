# VM Git Self-Healing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the VM autonomously activate and confirm the newest safe `origin/main` commit without losing local session data when Git is detached, rebasing, ahead, behind, or diverged.

**Architecture:** Add a focused `tools/git_sync.py` state machine that owns repository recovery and publication. The watcher calls it before spawning the bot and after committing session data; the batch file delegates final backup to the watcher. `main.py` reports the active Git state through the existing Telegram notification path after MT5 and Telegram are connected.

**Tech Stack:** Python 3.11/3.14, subprocess Git CLI, dataclasses, pytest, Windows batch.

---

### Task 1: Git State Machine

**Files:**
- Create: `tools/git_sync.py`
- Create: `tests/test_git_sync.py`

- [ ] **Step 1: Write real-repository failing tests**

Create temporary bare remotes and clones. Cover detached/equal, detached/local-ahead, remote-ahead, stale rebase, data-only divergence, and non-data divergence. Assert the public result contract:

```python
result = git_sync.synchronize_repository(repo, publish_local=True)
assert result.ok is True
assert result.branch == "main"
assert result.local_head == result.remote_head
assert result.action == "attached_and_pushed"
```

For non-data divergence, assert that a `vm-rescue-*` branch preserves the old local HEAD and `main` activates `origin/main`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_git_sync.py
```

Expected: collection/import failure because `tools.git_sync` does not exist.

- [ ] **Step 3: Implement the minimal synchronizer**

Define:

```python
@dataclass(frozen=True)
class SyncResult:
    ok: bool
    action: str
    branch: str | None
    local_head: str | None
    remote_head: str | None
    rescue_branch: str | None = None
    error: str | None = None

```

Expose `synchronize_repository(repo_dir: Path, *, remote: str = "origin",
branch: str = "main", publish_local: bool = True) -> SyncResult`. Use
`git merge-base --is-ancestor` for relation classification. Before quitting a
stale rebase, preserve HEAD in a unique `vm-rescue-*` branch. Use
`git switch -C main <target>` and `git push origin HEAD:main`; never use an
unbounded pull. Auto-publish only commits whose subjects start with `data:`
and whose changed paths stay under `data/`. A data conflict aborts and blocks
startup. Non-data commits switch to remote only after a rescue branch exists.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
python -m pytest -q tests/test_git_sync.py
```

Expected: all Git state tests pass.

- [ ] **Step 5: Commit**

```powershell
git add tools/git_sync.py tests/test_git_sync.py
git commit -m "watch: add deterministic git synchronization"
```

### Task 2: Watcher Integration And Launch Gate

**Files:**
- Modify: `tools/run_bot_watch.py`
- Modify: `tests/test_run_bot_watch.py`

- [ ] **Step 1: Write failing watcher tests**

Add tests that inject a `SyncResult` and prove:

```python
assert watch._prepare_repository_for_runtime().ok is True
assert watch.main() == watch.WATCHER_GIT_BLOCKED_EXIT_CODE
assert spawn_calls == []
```

The blocked case must not call `_spawn_bot`. The successful case must print the exact active commit and branch. Session publication must invoke the synchronizer and publish `HEAD:main` through it rather than calling `pull --rebase` or `push origin main` directly.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
python -m pytest -q tests/test_run_bot_watch.py
```

Expected: failures for missing preflight and launch gate.

- [ ] **Step 3: Integrate the synchronizer**

Import `tools.git_sync`, add `WATCHER_GIT_BLOCKED_EXIT_CODE = 76`, and call the synchronizer before `_spawn_bot`. Replace `_pull_main_ff`, `_pull_main_and_refresh_heads`, and the post-commit pull/rebase/push block with one synchronization call. Return a structured result from `_push_session_data` so callers refresh their known heads from verified state.

- [ ] **Step 4: Verify watcher tests**

```powershell
python -m pytest -q tests/test_run_bot_watch.py
```

Expected: all watcher tests pass.

- [ ] **Step 5: Commit**

```powershell
git add tools/run_bot_watch.py tests/test_run_bot_watch.py
git commit -m "watch: gate bot launch on verified main"
```

### Task 3: Single Final-Backup Path

**Files:**
- Modify: `tools/run_bot_watch.py`
- Modify: `run_bot.bat`
- Modify: `tests/test_run_bot_watch.py`
- Modify: `tests/test_run_bot_bat.py`

- [ ] **Step 1: Write failing CLI and batch tests**

Assert that `cli(["--final-backup"])` invokes `_push_session_data` and returns non-zero when publication is unsafe. Assert batch text contains no `git pull --rebase`, no `git push origin main`, and delegates recovery with:

```bat
python -u tools\run_bot_watch.py --final-backup
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest -q tests/test_run_bot_watch.py tests/test_run_bot_bat.py
```

Expected: failures because the CLI mode and batch delegation do not exist.

- [ ] **Step 3: Implement CLI delegation and interruption recovery**

Add `cli(argv=None)` with normal and `--final-backup` modes. The final-backup mode calls the same data builder/publication path and exits according to its `SyncResult`. Replace the duplicated inline Python and Git commands in `run_bot.bat` with that mode. Snapshot mutable offline reports before regeneration and restore the snapshot only when `KeyboardInterrupt` or `SystemExit` aborts the pipeline, preventing deletion-only working trees.

- [ ] **Step 4: Verify CLI and batch tests**

```powershell
python -m pytest -q tests/test_run_bot_watch.py tests/test_run_bot_bat.py
```

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```powershell
git add tools/run_bot_watch.py run_bot.bat tests/test_run_bot_watch.py tests/test_run_bot_bat.py
git commit -m "watch: unify final backup and git recovery"
```

### Task 4: Telegram Version Confirmation

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main_helpers.py`

- [ ] **Step 1: Write the failing message test**

Add a pure formatter test:

```python
text = main._startup_status_message({
    "git_commit": "0457a0e",
    "git_branch": "main",
    "git_dirty": False,
})
assert "BOT ACTIVO" in text
assert "0457a0e" in text
assert "main" in text
assert "MT5 conectado" in text
assert "Canales 1 y 2 activos" in text
```

- [ ] **Step 2: Run test and verify RED**

```powershell
python -m pytest -q tests/test_main_helpers.py
```

Expected: failure because `_startup_status_message` is missing.

- [ ] **Step 3: Implement and send after successful connections**

Create the pure formatter and call the existing `listener.notify()` only after `executor.init()`, `client.start()`, and `client.get_me()` have succeeded. Journal `startup_version_confirmed` with the same Git fields before sending.

- [ ] **Step 4: Verify helper tests**

```powershell
python -m pytest -q tests/test_main_helpers.py
```

Expected: all helper tests pass.

- [ ] **Step 5: Commit**

```powershell
git add main.py tests/test_main_helpers.py
git commit -m "watch: notify active production version"
```

### Task 5: Full Verification And Publication Readiness

**Files:**
- Verify all modified files

- [ ] **Step 1: Compile changed Python modules**

```powershell
python -m py_compile tools/git_sync.py tools/run_bot_watch.py main.py
```

Expected: exit code 0.

- [ ] **Step 2: Run the complete test suite**

```powershell
python -m pytest -q
```

Expected: no failures.

- [ ] **Step 3: Inspect repository state**

```powershell
git diff --check
git status -sb
git log --oneline --decorate -6
```

Expected: no whitespace errors and only intentional commits ahead of `origin/main`.

- [ ] **Step 4: Keep deployment controlled**

Do not push while the production watcher is processing live signals. Publish to
`origin/main` only when the user confirms a safe restart window; then verify
that the VM reports `main`, matching local/remote commits, and a single watcher
plus one `main.py` process.
