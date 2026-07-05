# Tick Replay Validator v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate each real MT5 ticket against cached bid/ask ticks before strategy optimization begins.

**Architecture:** Add a second replay audit layer after accounting replay. `tick_replay_validator.py` reads `data/replay_trades.jsonl` and `data/ticks_cache/YYYY-MM-DD.parquet`, walks ticks chronologically, applies confirmed SL/TP changes by timestamp, and checks whether the first touched level matches the MT5 close reason.

**Tech Stack:** Python 3, pytest, pandas/parquet, JSONL, existing tick cache and watcher.

---

### Task 1: Ticket-Level Tick Replay Core

**Files:**
- Create: `tick_replay_validator.py`
- Test: `tests/test_tick_replay_validator.py`

- [x] **Step 1: Write failing tests**

Cover BUY TP by bid, SELL SL by ask, chronological SL/TP activation, and missing tick data.

- [x] **Step 2: Run targeted tests**

Run: `python -m pytest tests/test_tick_replay_validator.py -q`

Expected: failure because `tick_replay_validator.py` does not exist.

- [x] **Step 3: Implement minimal replay core**

Implement `validate_ticket()`, `validate_trade()`, parquet loading, JSONL output, and summary status.

- [x] **Step 4: Run targeted tests**

Run: `python -m pytest tests/test_tick_replay_validator.py -q`

Expected: pass.

### Task 2: Watcher Integration

**Files:**
- Modify: `tools/run_bot_watch.py`
- Modify: `run_bot.bat`
- Test: `tests/test_run_bot_watch.py`

- [x] **Step 1: Write failing tests**

Assert the watcher runs `tick_replay_validator.py --quiet` and stages `data/tick_replay_audit.jsonl` plus `data/tick_replay_status.json`.

- [x] **Step 2: Run targeted tests**

Run: `python -m pytest tests/test_run_bot_watch.py::test_regenerate_tick_replay_audit_accepts_blocked_report tests/test_run_bot_watch.py::test_push_session_data_adds_reconcile_status -q`

Expected: failure until watcher integration exists.

- [x] **Step 3: Implement watcher integration**

Run tick replay after tick cache/readiness generation. Treat non-zero exit as an alert if audit/status files were produced.

- [x] **Step 4: Verify**

Run: `python -m pytest -q`

Expected: all tests pass.
