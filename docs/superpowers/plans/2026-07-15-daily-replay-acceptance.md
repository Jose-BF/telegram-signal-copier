# Daily Replay Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish an honest per-day replay acceptance contract while separating exact MT5 accounting, verified tick paths and observed execution slippage.

**Architecture:** Evolve the existing offline replay pipeline rather than adding a parallel simulator. Scope tick assurance, observed replay, readiness and the strategy farm to one shared date boundary; keep all live execution modules untouched.

**Tech Stack:** Python 3, pytest, JSON/JSONL, pandas/parquet, existing MT5 tick cache and watcher.

---

### Task 1: Correct observed execution semantics

**Files:**
- Modify: `observed_tick_replay_validator.py`
- Modify: `tests/test_observed_tick_replay_validator.py`

- [ ] Add failing tests proving a valid causal path remains exact when the
  observed MT5 fill differs from the contemporaneous quote, while the delta is
  retained as a warning.
- [ ] Add a failing test for MT5 close reason `other` replayed as an external
  market close with nearby ticks.
- [ ] Preserve failing tests for missing/invalid contracts, missing ticks,
  wrong first-touch reason and shifted timelines.
- [ ] Implement `causal_path_v2`, MT5-deal fill authority, execution-delta
  warnings and external-close handling.
- [ ] Run `python -m pytest tests/test_observed_tick_replay_validator.py -q`.
- [ ] Commit the focused change.

### Task 2: Make readiness strict and daily

**Files:**
- Modify: `replay_readiness_report.py`
- Modify: `tests/test_replay_readiness_report.py`

- [ ] Add failing tests requiring valid UTC-v3 sidecars instead of file
  existence.
- [ ] Add failing tests for `--since`, per-day summaries, observed-path
  evidence and pending open positions.
- [ ] Implement strict contract loading, observed-audit joining, selected
  scope metadata and `ready`/`pending`/`blocked` cohort summaries.
- [ ] Keep exact MT5 `diff == 0.00` distinct from journal-health warnings.
- [ ] Run `python -m pytest tests/test_replay_readiness_report.py -q`.
- [ ] Commit the focused change.

### Task 3: Align every offline builder to one scope

**Files:**
- Modify: `tools/ensure_replay_tick_cache.py`
- Modify: `observed_tick_replay_validator.py`
- Modify: `tools/run_bot_watch.py`
- Modify: `tests/test_ensure_replay_tick_cache.py`
- Modify: `tests/test_run_bot_watch.py`

- [ ] Add failing tests for explicit scope metadata and selected trade counts
  in tick-cache status.
- [ ] Add failing watcher tests proving tick assurance, observed replay,
  readiness and strategy farm receive the same start date and run in causal
  order.
- [ ] Introduce `SIMULATION_FROM_DATE`, falling back to the existing farm
  environment variable, and pass it to every relevant CLI.
- [ ] Reorder observed replay before readiness and preserve best-effort bot
  restart behavior.
- [ ] Run the three focused test modules.
- [ ] Commit the focused change.

### Task 4: Verify artifacts and integrate

**Files:**
- Modify only generated compact `data/*.json` artifacts that can be rebuilt
  without pretending local tick files exist.

- [ ] Run the selected-window dry-run and confirm local missing tick files are
  reported honestly because Parquet retention is VM-local.
- [ ] Run `python -m py_compile` for every modified Python module.
- [ ] Run `python -m pytest -q` and require zero failures and zero skips.
- [ ] Run `git diff --check`, inspect the full diff, and request independent
  code review.
- [ ] Resolve every critical or important review finding and rerun tests.
- [ ] Merge the verified feature branch into current `origin/main`, push
  `main`, and provide exact VM update and end-of-day verification commands.

