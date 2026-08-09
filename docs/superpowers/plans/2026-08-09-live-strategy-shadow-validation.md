# Live Strategy And Shadow Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy an auditable Dubai basket guard and explicit-activation-only Gold zone policy while repairing the evidence paths needed to evaluate the next session.

**Architecture:** Pure decision helpers define live policy transitions and are called by existing MT5/Telegram orchestration. Live execution continues through the durable pending-action queue. Offline canonicalization and money conversion remain isolated from runtime modules.

**Tech Stack:** Python 3.14, asyncio, MetaTrader5 Python API, pytest, JSONL causal journal.

---

### Task 1: Stabilize the existing zone-clock test

**Files:**
- Modify: `tests/test_canal2_zone_lifecycle.py`

- [ ] Make the fixture expiry relative to a controlled current time instead of a fixed past date.
- [ ] Run `python -m pytest tests/test_canal2_zone_lifecycle.py -q` and expect all tests to pass.
- [ ] Commit only the test stabilization.

### Task 2: Preserve verified money-clock evidence

**Files:**
- Modify: `strategy_simulator.py`
- Modify: `strategy_farm.py`
- Modify: `tests/test_strategy_farm.py`
- Modify: `tests/test_strategy_simulator.py`

- [ ] Add a failing test proving the per-day `utc_offset_seconds` reaches `BrokerMoneyConverter.convert_leg`.
- [ ] Run the focused test and confirm it fails because the argument is absent.
- [ ] Thread `verified_utc_offset_seconds` through the simulator and include it in result-cache identity.
- [ ] Extract the offset from the verified market tick contract for each executed trade.
- [ ] Run both focused test modules and expect them to pass.
- [ ] Commit the money-clock fix.

### Task 3: Canonicalize coherent provider price bundles

**Files:**
- Modify: `provider_signal_catalog.py`
- Modify: `tests/test_provider_signal_catalog.py`

- [ ] Add failing regressions for a stale `4059-4064` bundle corrected to `4259-4264`, and for an unexecuted stale bundle with independent market context.
- [ ] Confirm the current canonicalizer retains the wrong hundreds prefix.
- [ ] Select a bundle-level prefix only when direction, range, targets, stop, and independent context produce one unambiguous candidate.
- [ ] Preserve both raw and repaired evidence in `canonicalization_issues`.
- [ ] Run the provider catalog tests and expect them to pass.
- [ ] Commit the canonicalization fix.

### Task 4: Make Gold zone first touches observation-only

**Files:**
- Modify: `config.py`
- Modify: `canal2_zone_lifecycle.py`
- Modify: `listener.py`
- Modify: `tests/test_canal2_zone_execution.py`
- Modify: `tests/test_canal2_zone_lifecycle.py`

- [ ] Add failing tests proving first touch records one shadow observation without opening and explicit `Active` still opens exactly once.
- [ ] Confirm the current first-touch test opens a position.
- [ ] Add explicit feature switches and durable `canal2_zone_first_touch_observed` evidence.
- [ ] Keep the plan armed after first touch and allow explicit activation of the same generation.
- [ ] Run both Gold zone test modules and expect them to pass.
- [ ] Commit the Gold trigger-policy change.

### Task 5: Add the Dubai basket guard

**Files:**
- Create: `live_basket_guard.py`
- Modify: `config.py`
- Modify: `state.py`
- Modify: `listener.py`
- Modify: `position_lifecycle_monitor.py`
- Create: `tests/test_live_basket_guard.py`
- Modify: `tests/test_position_lifecycle_monitor.py`

- [ ] Add failing pure tests for no action, loss-cap close, trail arm, trail-floor close, channel isolation, and idempotency.
- [ ] Confirm they fail because the guard does not exist.
- [ ] Implement the pure state transition and validated configuration contract.
- [ ] Add failing integration tests proving the monitor queues all current tickets once and emits the full journal decision.
- [ ] Integrate the guard into monitor startup and the live P/L sampling path.
- [ ] Run all guard and lifecycle-monitor tests and expect them to pass.
- [ ] Commit the Dubai guard.

### Task 6: Publish the strategy contract and verify the repository

**Files:**
- Modify: `main.py`
- Modify: `README.md`
- Modify: `tests/test_main_startup.py`

- [ ] Add a failing test for one startup journal event containing all live strategy switches and thresholds.
- [ ] Publish a human-readable startup line and a machine-readable journal contract.
- [ ] Run the focused startup tests.
- [ ] Run `python -m pytest -q` and require zero failures.
- [ ] Inspect `git diff`, `git status`, and the commit list; exclude all pre-existing local replay edits.
- [ ] Fetch `origin/main`, verify fast-forward ancestry, push `HEAD:main`, and verify the remote commit hash.

