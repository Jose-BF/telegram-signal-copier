# Runtime Telemetry Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove runtime logs and generated data from the production Git worktree while preserving lossless replay evidence and automatic remote delivery.

**Architecture:** Seed an ignored append-only runtime store from the current tracked corpus, export deterministic incremental chunks, and publish them from an isolated telemetry checkout. The watcher keeps code synchronization but never commits or waits for data transport.

**Tech Stack:** Python 3.11+, standard-library pathlib/hashlib/gzip/json/subprocess, Git CLI, Windows batch, pytest.

---

### Task 1: Runtime Path and Migration Contract

**Files:**
- Create: `runtime_paths.py`
- Create: `tests/test_runtime_paths.py`
- Modify: `.gitignore`

- [x] Write failing tests proving first launch atomically seeds authoritative
  files, preserves an existing runtime file, records hashes and selects the
  runtime directory only when initialized.
- [x] Run `python -m pytest tests/test_runtime_paths.py -q` and confirm the
  tests fail because `runtime_paths` does not exist.
- [x] Implement the path resolver and idempotent migration with no Git calls.
- [x] Run the focused tests and confirm they pass.

### Task 2: Route Production Evidence Away From Git

**Files:**
- Modify: `journal.py`
- Modify: `main.py`
- Modify: `pending_actions.py`
- Modify: `runtime_control.py`
- Modify: `config.py`
- Modify: `tests/test_journal.py`
- Modify: `tests/test_main_startup.py`

- [x] Write failing tests proving production writes use the runtime store and
  the orphan finalizer reads the same event stream.
- [x] Run the focused tests and confirm the legacy `data/` paths fail them.
- [x] Replace duplicated path construction with `runtime_paths` constants.
- [x] Run the focused runtime tests and confirm no tracked fixture is changed.

### Task 3: Immutable Telemetry Chunks

**Files:**
- Create: `tools/runtime_telemetry.py`
- Create: `tests/test_runtime_telemetry.py`

- [x] Write failing tests for complete-line chunking, deterministic retry,
  atomic cursor updates, partial tails, multiple streams and hash validation.
- [x] Run `python -m pytest tests/test_runtime_telemetry.py -q` and confirm the
  module is missing.
- [x] Implement local checkpoint, isolated-branch publication and verified
  materialization. All subprocess calls receive finite timeouts.
- [x] Run the focused tests, including a temporary bare Git remote, and confirm
  publication never changes the source checkout HEAD or status.

### Task 4: Make Watcher Code-Only

**Files:**
- Modify: `tools/run_bot_watch.py`
- Modify: `tools/runtime_recovery.py`
- Modify: `tools/git_sync.py`
- Modify: `run_bot.bat`
- Modify: `tests/test_run_bot_watch.py`
- Modify: `tests/test_runtime_recovery.py`
- Modify: `tests/test_git_sync.py`
- Modify: `tests/test_run_bot_bat.py`

- [x] Replace checkpoint tests so they require a local telemetry checkpoint and
  forbid `git add`, `git commit` and `git push` against `main`.
- [x] Add a failing test proving publication failure cannot block spawn,
  restart or code update.
- [x] Implement migration-before-Git, remove local data-commit publication and
  launch the isolated publisher without waiting for it.
- [x] Simplify the batch recovery path to a local checkpoint that always leaves
  code restart independent from network state.
- [x] Run all watcher, recovery and Git tests.

### Task 5: Replay Inputs and Operational Documentation

**Files:**
- Modify: `reconcile_mt5_ledger.py`
- Modify: `build_replay_trades.py`
- Modify: `provider_signal_catalog.py`
- Modify: `recursive_log_learning.py`
- Modify: `tools/analyze_new_logs.py`
- Modify: `README.md`

- [x] Add failing default-path tests proving analysis consumes a verified
  materialized runtime corpus without modifying the production stream.
- [x] Route authoritative inputs through `runtime_paths`; preserve explicit CLI
  overrides and historical fallback.
- [x] Document VM upgrade, telemetry status, manual checkpoint and local pull
  commands in plain language.
- [x] Run replay-focused tests.

### Task 6: Verification and Publication

- [x] Run `python -m py_compile` for every changed production module.
- [x] Run the complete `python -m pytest -q` suite.
- [x] Verify `git status --porcelain` contains only intended source, test and
  documentation changes and no generated runtime data.
- [ ] Commit the implementation and push the exact verified commit to
  `origin/main`.
