# Dubai Balanced V1 Live Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the frozen Dubai balanced candidate on demo with exact entry, management and recovery evidence while leaving Gold Signals unchanged.

**Architecture:** Add one pure live-policy module containing the immutable candidate contract and decisions. The listener creates a candidate-tagged `Signal`; the existing lifecycle monitor executes typed ladder legs and aggregate exits; startup and resync publish and restore the same contract. Live modules do not import research modules, while tests compare the pure live contract with the research fingerprint.

**Tech Stack:** Python 3.14, asyncio, MetaTrader5, pytest, existing journal/pending-action/runtime modules.

---

### Task 1: Freeze And Validate The Live Contract

**Files:**
- Create: `dubai_live_candidate.py`
- Modify: `config.py`
- Modify: `.env.example`
- Test: `tests/test_dubai_live_candidate.py`

- [ ] Write tests that instantiate the live policy and assert the exact
  fingerprint, three volumes, ladder step, expiry, basket stop, dynamic lock,
  loss-only time exit and validation failures.
- [ ] Run `python -m pytest tests/test_dubai_live_candidate.py -q` and confirm
  the test fails because the module does not exist.
- [ ] Implement immutable policy, leg-plan and pure guard-decision types, plus
  configuration parsing with candidate enabled by default for the requested
  demo trial.
- [ ] Re-run the focused test and confirm it passes.

### Task 2: Build Candidate Signals Without Immediate Scale-Out

**Files:**
- Modify: `state.py`
- Modify: `listener.py`
- Test: `tests/test_dubai_live_listener.py`

- [ ] Write tests for both Dubai opening paths proving the first order is
  0.01 lots, immediate scale-out is skipped, candidate identity is attached,
  planned levels are anchored to the real first fill and Gold is unchanged.
- [ ] Run the tests and confirm the old code opens immediate scale-out legs.
- [ ] Add candidate fields to `Signal`, centralize candidate initialization,
  start the monitor immediately after the first fill and journal the complete
  entry plan.
- [ ] Re-run the focused tests and confirm they pass.

### Task 3: Execute Typed Adverse Ladder Legs Exactly Once

**Files:**
- Modify: `position_lifecycle_monitor.py`
- Modify: `executor.py`
- Test: `tests/test_dubai_live_ladder.py`

- [ ] Write tests for BUY and SELL trigger sides, 0.04 volume, exact fill
  recording, sequential level execution, 15-minute expiry, failed-fill retry,
  exposure cap and no duplicate fill.
- [ ] Run the tests and confirm the legacy monitor cannot represent per-leg
  volume or expiry.
- [ ] Extend the monitor with typed entry plans and use
  `open_market_with_fill` for candidate legs while retaining the legacy DCA
  path unchanged.
- [ ] Re-run focused monitor/listener tests and confirm they pass.

### Task 4: Apply The Frozen Basket Exit Rules

**Files:**
- Modify: `live_basket_guard.py`
- Modify: `position_lifecycle_monitor.py`
- Test: `tests/test_dubai_live_guard.py`

- [ ] Write tests proving priority order: -25 EUR stop, +10 EUR arm, close at
  peak minus 2 EUR, 40-minute close only at non-positive P/L, no close at a
  positive 40-minute P/L and fail-closed behavior when money evidence is
  incomplete.
- [ ] Run tests and confirm the current fixed +30/+20 guard cannot satisfy the
  dynamic rule.
- [ ] Add candidate guard evaluation and journal fields without changing the
  legacy guard used when the candidate switch is off.
- [ ] Re-run focused guard and lifecycle tests and confirm they pass.

### Task 5: Preserve Provider Evidence Without Mutating The Candidate

**Files:**
- Modify: `listener.py`
- Test: `tests/test_dubai_live_management.py`

- [ ] Write tests proving provider levels, BE and SL moves are recorded but do
  not reach MT5, while explicit close instructions close the complete basket.
- [ ] Run tests and confirm current management applies TP/SL/BE.
- [ ] Gate level installation and management dispatch by the signal's frozen
  strategy identity, emitting an explicit ignored-by-strategy journal event.
- [ ] Re-run management, classifier and listener suites.

### Task 6: Make Startup And Restart Deterministic

**Files:**
- Modify: `executor.py`
- Modify: `main.py`
- Modify: `live_auditor.py`
- Test: `tests/test_dubai_live_recovery.py`
- Test: `tests/test_main_startup.py`

- [ ] Write tests for demo-only startup, strategy-contract publication,
  recovery before/after one ladder fill, expired recovery and expected-leg
  auditing.
- [ ] Run tests and confirm candidate state is currently lost on restart.
- [ ] Publish account trade mode, enforce demo-only activation, restore the
  candidate plan from journal/MT5 and restart only missing non-expired levels.
- [ ] Re-run startup, auditor and recovery tests.

### Task 7: Verify Research Parity And Repository Safety

**Files:**
- Modify: `README.md`
- Test: `tests/test_dubai_live_candidate.py`

- [ ] Compare the live rule payload with `research.dubai_iterative` and assert
  fingerprint `32cb5c0f...60631`.
- [ ] Run all new focused tests, then `python -m pytest -q`.
- [ ] Inspect `git diff --check`, `git status -sb` and the complete diff.
- [ ] Commit only reviewed code and documentation. Do not push, restart the VM
  or change live account state until the user receives the verified result.
