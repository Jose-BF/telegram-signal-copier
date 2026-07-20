# Telegram Graph Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send human-review alerts as a truthful price chart plus a compact caption, while preserving the existing text alert as a guaranteed fallback.

**Architecture:** A focused `alert_graphics.py` module collects a bounded recent MT5 tick window and renders only verified prices into PNG bytes. `listener.py` uploads the image without a caption, then adds the Unicode caption through Telegram JSON; any dependency, MT5, render, upload, or caption error immediately falls back to the current text notification without changing trading state.

**Tech Stack:** Python 3.11+, MetaTrader5, Pillow, Telegram Bot HTTP API, pytest.

---

### Task 1: Truthful chart model and renderer

**Files:**
- Create: `alert_graphics.py`
- Create: `tests/test_alert_graphics.py`
- Modify: `requirements.txt`

- [x] Write failing tests proving that the chart model uses the real provider name, selects the next directional TP, never invents a trajectory when no ticks exist, and renders valid PNG bytes.
- [x] Run `python -m pytest tests/test_alert_graphics.py -q` and confirm the missing module/function failures.
- [x] Implement bounded MT5 tick collection, deterministic downsampling, chart-model construction, and Pillow rendering with Windows-font and default-font fallback.
- [x] Add `Pillow>=10.0.0` to `requirements.txt`; keep imports lazy so a VM missing Pillow still runs with text alerts.
- [x] Run `python -m pytest tests/test_alert_graphics.py -q` and confirm all renderer tests pass.

### Task 2: UTF-8-safe photo transport

**Files:**
- Create: `telegram_notifications.py`
- Create: `tests/test_telegram_notifications.py`

- [x] Write failing transport tests that inspect both requests: `sendPhoto` contains only ASCII metadata and PNG bytes, while `editMessageCaption` carries the exact Unicode caption as JSON.
- [x] Run `python -m pytest tests/test_telegram_notifications.py -q` and confirm the missing transport failure.
- [x] Implement the two-step Bot API transport using only the standard library, returning the Telegram message id and raising a typed error for either stage.
- [x] Run `python -m pytest tests/test_telegram_notifications.py -q` and confirm both UTF-8 and error-path tests pass.

### Task 3: Review-alert integration with fallback

**Files:**
- Modify: `listener.py`
- Modify: `tests/test_telegram_perception.py`

- [x] Write failing async tests proving a review alert prefers the graph, logs `notify_graph_sent`, and falls back exactly once to `notify(text)` when collection, rendering, dependency loading, upload, or caption editing fails.
- [x] Run the focused tests and confirm they fail at the missing graph integration.
- [x] Add a bounded graph-build timeout, call the two-step photo transport in a worker thread, and retain `format_review_notification()` as the fallback caption/text source.
- [x] Ensure graph generation never calls order APIs and never changes `Signal`, pending actions, MT5 positions, or simulation data.
- [x] Run `python -m pytest tests/test_telegram_perception.py tests/test_alert_graphics.py tests/test_telegram_notifications.py -q`.

### Task 4: Verification

**Files:**
- Verify only; no generated runtime data is committed.

- [x] Render BUY and SELL sample images and visually verify provider, direction, current price, entry/BE, actual SL, next TP, P&L, position count, and Unicode.
- [x] Run `python -m pytest -q` and require zero failures.
- [x] Run `git diff --check` and confirm no `data/` files changed.
- [x] Record the one-time VM dependency command `python -m pip install -r requirements.txt` for deployment.
