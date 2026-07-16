# Session-Aware Replay And Level Drift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make replay respect Vantage XAUUSD trading sessions and capture unattributed live SL/TP changes without touching order behavior.

**Architecture:** A small pure session-calendar module filters in-memory replay ticks using the UTC offset already verified in each cache sidecar. The existing read-only live auditor tracks actual per-ticket levels and compares changes against confirmed signal state and pending bot actions.

**Tech Stack:** Python 3, pandas, NumPy, pytest, MetaTrader5 read-only position snapshots.

---

### Task 1: Vantage XAUUSD Session Calendar

**Files:**
- Create: `broker_market_sessions.py`
- Modify: `observed_tick_replay_validator.py`
- Test: `tests/test_broker_market_sessions.py`
- Test: `tests/test_observed_tick_replay_validator.py`

- [ ] Write a failing test proving that UTC Sunday 22:00 with a UTC+3 broker
  offset is outside the Monday session and 22:01 is inside it.
- [ ] Run `python -m pytest tests/test_broker_market_sessions.py -q` and verify
  it fails because the session module does not exist.
- [ ] Implement `filter_tradable_ticks()` with the explicit
  `vantage_xauusd_standard_v1` weekly windows.
- [ ] Add the verified sidecar offset to each loaded frame, filter it in
  `ReplayTickFrameCache`, and expose the contract id plus filtered count.
- [ ] Add a regression fixture in the observed validator tests where a TP is
  crossed by a quote-only tick at 22:00 but closes on the tradable path after
  22:01.
- [ ] Run the two focused test files and verify they pass.

### Task 2: Require Session Evidence Downstream

**Files:**
- Modify: `replay_readiness_report.py`
- Modify: `strategy_farm.py`
- Test: `tests/test_replay_readiness_report.py`
- Test: `tests/test_strategy_farm.py`

- [ ] Write failing tests showing that a legacy exact audit without a market
  session contract cannot satisfy readiness or the farm's current causal
  contract gate.
- [ ] Add `market_session_contract` to observed reports and require
  `vantage_xauusd_standard_v1` downstream.
- [ ] Run both focused test files and verify they pass.

### Task 3: Confirmed Per-Ticket TP State

**Files:**
- Modify: `state.py`
- Modify: `pending_actions.py`
- Test: `tests/test_pending_actions.py`

- [ ] Write a failing test for a helper that records confirmed `new_sl` and
  `new_tp` into `sl_by_ticket` and `tp_by_ticket` only after an MT5 OK result.
- [ ] Add `tp_by_ticket` to `Signal` and call the helper from the queue's DONE
  path.
- [ ] Run `python -m pytest tests/test_pending_actions.py -q` and verify it
  passes.

### Task 4: Read-Only Level Drift Chivato

**Files:**
- Modify: `live_auditor.py`
- Test: `tests/test_live_auditor.py`

- [ ] Write failing tests proving the first level snapshot is silent, an
  unexplained later change emits one `mt5_level_change_unattributed`, and a
  confirmed or pending bot change remains silent.
- [ ] Store previous actual levels in `LiveAuditor`, include actual levels in
  existing snapshots, and compare transitions with signal and pending state.
- [ ] Emit one forensic event plus warning anomaly without changing the
  position or `Signal` state.
- [ ] Run `python -m pytest tests/test_live_auditor.py -q` and verify it passes.

### Task 5: Precise Historical Blocker And Verification

**Files:**
- Modify: `observed_tick_replay_validator.py`
- Test: `tests/test_observed_tick_replay_validator.py`

- [ ] Write a failing test where MT5 reports an SL close but recorded SL
  history never reaches the close; require
  `missing_sl_transition_evidence:<ticket>`.
- [ ] Replace the generic no-touch blocker only for this evidenced condition;
  retain mismatch status and MT5 fill authority.
- [ ] Run all focused tests.
- [ ] Run `python -m pytest -q` and require the complete suite to pass.
- [ ] Inspect `git diff --check`, `git status -sb`, and the final diff before
  preparing the production update.

