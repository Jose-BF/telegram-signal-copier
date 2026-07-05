# Week Replay Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every trading day auditable before Friday by detecting missing replay data as soon as it appears.

**Architecture:** Keep the current `ledger -> replay_trades -> accounting_replay_audit` pipeline, then add a readiness layer that checks whether each trade has the market data and forensic fields needed for full replay. Tick parquet files stay local under `data/ticks_cache/`; only compact status/report JSON files are committed.

**Tech Stack:** Python 3, pytest, JSONL/JSON, pandas/parquet, MetaTrader5 `copy_ticks_range`, existing watcher.

---

### Task 1: Persist MT5 Deal Detail

**Files:**
- Modify: `reconcile_mt5_ledger.py`
- Modify: `build_replay_trades.py`
- Test: `tests/test_reconcile_mt5_ledger.py`
- Test: `tests/test_build_replay_trades.py`

- [ ] **Step 1: Write failing tests**

Add tests that expect each position to carry `pnl_components`, `open_deal`, `close_deal`, and raw `deals`.

- [ ] **Step 2: Run targeted tests**

Run: `python -m pytest tests/test_reconcile_mt5_ledger.py::TestLoadMt5PositionsDealDetail tests/test_build_replay_trades.py::test_ticket_preserves_mt5_deal_detail -q`

Expected: failure because the new fields are missing.

- [ ] **Step 3: Implement minimal code**

Extend `load_mt5_positions()` to store deal components and extend `_normalise_ticket()` to preserve them.

- [ ] **Step 4: Run targeted tests**

Run: `python -m pytest tests/test_reconcile_mt5_ledger.py::TestLoadMt5PositionsDealDetail tests/test_build_replay_trades.py::test_ticket_preserves_mt5_deal_detail -q`

Expected: pass.

### Task 2: Tick Cache Backfill Tool

**Files:**
- Modify: `mt5_tick_cache.py`
- Create: `tools/ensure_replay_tick_cache.py`
- Test: `tests/test_ensure_replay_tick_cache.py`

- [ ] **Step 1: Write failing tests**

Add tests for deriving required UTC days from replay trade windows, dry-run status output, and local cache status.

- [ ] **Step 2: Run targeted tests**

Run: `python -m pytest tests/test_ensure_replay_tick_cache.py -q`

Expected: failure because the tool does not exist.

- [ ] **Step 3: Implement minimal code**

Create a CLI that reads `data/replay_trades.jsonl`, derives UTC days from open/close windows, and either dry-runs or downloads missing days into `data/ticks_cache/`.

- [ ] **Step 4: Run targeted tests**

Run: `python -m pytest tests/test_ensure_replay_tick_cache.py -q`

Expected: pass.

### Task 3: Daily Readiness Report

**Files:**
- Create: `replay_readiness_report.py`
- Test: `tests/test_replay_readiness_report.py`

- [ ] **Step 1: Write failing tests**

Add tests that produce one ready trade and one blocked trade with missing tick cache.

- [ ] **Step 2: Run targeted tests**

Run: `python -m pytest tests/test_replay_readiness_report.py -q`

Expected: failure because the report module does not exist.

- [ ] **Step 3: Implement minimal code**

Create a CLI that merges `replay_trades.jsonl` and `accounting_replay_audit.jsonl`, checks required fields and tick-cache coverage, and writes `data/replay_readiness_report.json`.

- [ ] **Step 4: Run targeted tests**

Run: `python -m pytest tests/test_replay_readiness_report.py -q`

Expected: pass.

### Task 4: Watcher Integration

**Files:**
- Modify: `tools/run_bot_watch.py`
- Modify: `run_bot.bat`
- Test: `tests/test_run_bot_watch.py`

- [ ] **Step 1: Write failing tests**

Add tests that the watcher regenerates tick-cache status and weekly readiness status after replay/audit.

- [ ] **Step 2: Run targeted tests**

Run: `python -m pytest tests/test_run_bot_watch.py -q`

Expected: failure until the watcher knows the new commands.

- [ ] **Step 3: Implement minimal code**

Call the tick cache tool and readiness report after `accounting_replay_validator.py`, and include their status JSON files in the data push.

- [ ] **Step 4: Run full tests**

Run: `python -m pytest -q`

Expected: all tests pass.
