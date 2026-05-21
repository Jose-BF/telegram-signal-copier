# Caja Negra y Replay Forense Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add forensic-grade logging and ledger reconstruction without changing trading behavior.

**Architecture:** The bot remains event-sourced through `journal.event()`. MT5 request/result and strategy snapshot events are appended to `trade_events.jsonl`; `reconcile.py` consumes them into per-position lifecycle fields and derived time-stop outcomes.

**Tech Stack:** Python, pytest, MetaTrader5 Python API, JSONL ledger.

---

### Task 1: Journal outcome category and strategy snapshot event

**Files:**
- Modify: `journal.py`
- Modify: `listener.py`
- Test: `tests/test_journal.py`
- Test: `tests/test_reconcile.py`

- [ ] Add `outcome` to `journal.CATEGORIES`.
- [ ] Emit `strategy_snapshot` when canal1/canal2 `Signal` objects are created.
- [ ] Verify old anomaly validation still rejects unknown categories.

### Task 2: Pending action lifecycle events

**Files:**
- Modify: `pending_actions.py`
- Test: `tests/test_pending_actions.py`

- [ ] Emit request events when modify/close/cancel actions enter the queue.
- [ ] Emit confirmed result events when MT5 returns OK or POSITION_GONE.
- [ ] Keep existing retry and failure behavior unchanged.

### Task 3: MT5 open order request/result events

**Files:**
- Modify: `executor.py`
- Test: `tests/test_executor_anomalies.py`

- [ ] Add a helper that derives `sig_id` from MT5 comments.
- [ ] Emit `mt5_order_requested` before `order_send` for market and pending opens.
- [ ] Emit `mt5_order_result` after `order_send`, including retcode/order/deal/price/bid/ask.
- [ ] Never raise if journaling fails.

### Task 4: Ledger lifecycle reconstruction

**Files:**
- Modify: `reconcile.py`
- Test: `tests/test_reconcile.py`

- [ ] Parse lifecycle events into `{ticket: {"sl_history": [], "tp_history": []}}`.
- [ ] Attach histories to `positions[*]` in `reconcile_signal()`.
- [ ] Derive `strategy_snapshot` into each ledger row.
- [ ] Derive `post_time_stop_outcome` from timeline and MT5 close reasons.

### Task 5: Time-stop outcome marker

**Files:**
- Modify: `dca_monitor.py`
- Test: `tests/test_dca_monitor.py`

- [ ] When notify-only time-stop fires, emit `journal.anomaly(..., category="outcome", severity="warning")`.
- [ ] Preserve current notify-only behavior exactly.

### Task 6: Verification and commit

**Files:**
- No production files unless tests reveal small fixes.

- [ ] Run targeted tests for journal/reconcile/pending/executor/dca.
- [ ] Run full `pytest -q`.
- [ ] Confirm `data/trade_events.jsonl` and `data/ledger.jsonl` are not staged.
- [ ] Commit with a descriptive English message.
