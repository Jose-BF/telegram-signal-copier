# Runtime Safety and Alert Noise Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the observed runtime failures while preserving the live strategy and complete replay evidence.

**Architecture:** Add small, testable safety helpers at existing ownership boundaries: Telegram polling in `listener.py`, signal P/L accounting in the position monitor, and notification control in journal/runtime monitors. Every behavior change is introduced by a failing regression test and does not alter entry count, lot sizing, or provider strategy.

**Tech Stack:** Python 3.11+, asyncio, Telethon, MetaTrader5 API, pytest.

---

### Task 1: Keep fallback polling alive

**Files:**
- Modify: `listener.py`
- Modify: `main.py`
- Test: `tests/test_listener_helpers.py`
- Test: `tests/test_anomaly_categories.py`

- [ ] Add a static test that parses production calls to `journal.anomaly` and rejects literal categories absent from `journal.CATEGORIES`.
- [ ] Run the static test and confirm it fails on the existing `management` and `telegram` categories.
- [ ] Replace invalid categories with `channel_msg` and add a per-message polling boundary that records failure without advancing the message watermark.
- [ ] Add a supervised top-level polling loop that restarts after an unexpected exit while preserving cancellation semantics.
- [ ] Run the polling and category tests and confirm both channel isolation and retryability.
- [ ] Commit with `fix: keep telegram fallback polling alive`.

### Task 2: Preserve a TP already installed by MT5

**Files:**
- Modify: `listener.py`
- Test: `tests/test_listener_tp_preservation.py`

- [ ] Add a regression test where the requested TP equals the position TP but is now inside the broker stop distance.
- [ ] Confirm the test fails because the current TP-chase replaces that target.
- [ ] Inspect the live position before TP-chase; when its TP already matches the requested target within symbol precision, enqueue only the SL change.
- [ ] Add controls showing that a genuinely missing and invalid TP still follows the existing late-target behavior.
- [ ] Run focused listener tests and commit with `fix: preserve installed position targets`.

### Task 3: Roll back only explicit fresh duplicates

**Files:**
- Modify: `listener.py`
- Test: `tests/test_channel_msg_detectors.py`

- [ ] Add tests for the explicit phrase `This is not a new signal`, exact newest duplicate selection, ambiguous candidates, and deletion-only behavior.
- [ ] Confirm the explicit-retraction tests fail before implementation.
- [ ] Add deterministic phrase detection and a bounded same-channel candidate selector comparing direction, range, TPs, and SL.
- [ ] Close/cancel only a uniquely proven newest duplicate; otherwise keep positions unchanged and emit one human alert.
- [ ] Run detector and lifecycle tests and commit with `fix: retract proven duplicate signals`.

### Task 4: Protect Dubai using total signal P/L

**Files:**
- Modify: `state.py`
- Modify: `position_lifecycle_monitor.py`
- Modify: `live_basket_guard.py`
- Modify: `config.py`
- Test: `tests/test_position_lifecycle_monitor_basket_guard.py`
- Test: `tests/test_live_basket_guard.py`

- [ ] Add a test with `+11.13` realized and `+22.16` floating that must arm the `+30` guard.
- [ ] Add tests for commission/swap/fee inclusion, closed-ticket caching, and incomplete history.
- [ ] Confirm the tests fail while the guard uses floating P/L only.
- [ ] Build a cached realized-P/L summary and evaluate the guard on `realized + floating` every 100 ms or faster.
- [ ] Prevent total-profit actions when history is incomplete, while retaining the known floating loss protection and explicit degraded evidence.
- [ ] Run guard tests and commit with `fix: guard total signal profit`.

### Task 5: Reduce human notification noise

**Files:**
- Modify: `pending_actions.py`
- Modify: `journal.py`
- Modify: `main.py`
- Modify: `config.py`
- Test: `tests/test_pending_actions.py`
- Test: `tests/test_journal.py`
- Test: `tests/test_broker_contract_runtime.py`

- [ ] Add tests proving tickets with cent-level entry differences aggregate by signal and reason.
- [ ] Add a test proving repeated critical alerts are Telegram-rate-limited while both journal anomalies remain.
- [ ] Add transition tests requiring persistent broker-money failure and suppressing recovery unless interruption was notified.
- [ ] Confirm all three groups fail against current behavior.
- [ ] Remove exact SL/TP values from the structural incident key and render grouped price ranges.
- [ ] Add a notification-only cooldown in the journal and failure hysteresis in the broker-money monitor.
- [ ] Run focused tests and commit with `fix: reduce actionable alert noise`.

### Task 6: Capture Telegram media evidence asynchronously

**Files:**
- Create: `telegram_media_evidence.py`
- Modify: `listener.py`
- Test: `tests/test_telegram_media_evidence.py`

- [ ] Add tests for non-media messages, asynchronous scheduling, SHA-256 naming, successful storage, and failed download evidence.
- [ ] Confirm the media tests fail because no capture service exists.
- [ ] Implement atomic local evidence storage and request/result journal events linked to channel, message, and edit revision.
- [ ] Schedule capture after receipt without awaiting it in the order execution path; keep OCR and inferred levels disabled.
- [ ] Run media and listener timing tests and commit with `feat: retain telegram media evidence`.

### Task 7: Verify the complete patch

**Files:**
- Review: all modified files

- [ ] Run all focused tests for polling, TP preservation, retraction, basket guard, alerts, and media.
- [ ] Run `pytest -q` and record the exact result.
- [ ] Restore test-generated runtime data and ensure `git status` contains code, tests, and docs only.
- [ ] Inspect the complete diff for strategy changes, credentials, generated logs, and unrelated worktree changes.
- [ ] Report the local commit and explicitly state that production remains unchanged until authorized.
