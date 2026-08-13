# Wednesday-Thursday Robustness Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the recurring interpretation and evidence failures observed on 2026-08-12 and 2026-08-13 without changing entry count, lot size, targets, or the base live strategy.

**Architecture:** Keep deterministic language classification separate from live MT5 execution. A pure basket planner proves whether partial closes can secure the signal before any order is queued. The causal auditor accepts asynchronous media evidence and transport duplicates only when their immutable Telegram identity agrees.

**Tech Stack:** Python 3.14, pytest, MetaTrader5 Python API, Telethon, JSONL causal events.

---

### Task 1: Provider language safety

**Files:**
- Modify: `classifier.py`
- Modify: `interpretation_firewall.py`
- Test: `tests/test_classifier.py`
- Test: `tests/test_interpretation_firewall.py`

- [ ] Add failing tests proving generic `risk free` becomes `SECURE_BASKET`, explicit BE remains `MOVE_SL_TO_BE`, educational explanations are non-executable, and non-holder text cannot close a trade.
- [ ] Run the focused tests and confirm the failures describe the current unsafe behavior.
- [ ] Add the narrow deterministic rules and the independent firewall guard.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Mathematically proven basket protection

**Files:**
- Create: `risk_free_basket.py`
- Modify: `executor.py`
- Modify: `listener.py`
- Modify: `state.py`
- Modify: `position_lifecycle_monitor.py`
- Test: `tests/test_risk_free_basket.py`
- Test: `tests/test_listener_helpers.py`

- [ ] Add failing pure tests for feasible, already-secured, impossible, missing-SL, prior-realized-profit, and far-target-preservation scenarios.
- [ ] Run them and confirm the planner is absent.
- [ ] Implement the pure planner with a configurable account-currency safety buffer and at least one surviving runner.
- [ ] Add failing listener tests proving no MT5 action is queued when evidence is incomplete and only the proved tickets are queued when feasible.
- [ ] Add an executor snapshot containing current account-currency P&L, stop-loss floor, prior realized P&L, and target distance.
- [ ] Integrate `SECURE_BASKET`, record its proof, and classify its confirmed closes separately.
- [ ] Run focused tests.

### Task 3: Reply ancestry and management outcomes

**Files:**
- Modify: `listener.py`
- Modify: `journal.py`
- Test: `tests/test_listener_helpers.py`
- Test: `tests/test_journal.py`

- [ ] Add failing tests for a management reply whose parent media replies to the original signal.
- [ ] Add bounded reply-ancestry resolution for both providers without guessing between open signals.
- [ ] Add source-message identity to management-understanding events.
- [ ] Add backward-compatible management outcomes (`requested`, `deferred`, `ignored`, `failed`) while retaining legacy booleans.
- [ ] Run focused tests.

### Task 4: Causal certification

**Files:**
- Modify: `tools/audit_causal_lineage.py`
- Test: `tests/test_audit_causal_lineage.py`

- [ ] Add failing tests for media captured after the raw event and the same immutable revision observed as live edit and polling delivery.
- [ ] Accept later verified media evidence for the same revision.
- [ ] Separate immutable Telegram identity from delivery transport.
- [ ] Add regression coverage for changed pending-action coalescence and its terminal lineage.
- [ ] Run the complete causal-auditor test module.

### Task 5: Money units, broker time, and snapshot health

**Files:**
- Modify: `journal.py`
- Modify: `main.py`
- Modify: `tools/capture_broker_money_contract.py`
- Test: `tests/test_journal.py`
- Test: `tests/test_broker_contract_runtime.py`

- [ ] Add canonical account-currency metadata to JSON events while retaining legacy CSV columns.
- [ ] Persist verified broker UTC offset evidence with the final trade event.
- [ ] Make stale broker-snapshot health a stable state transition so skipped captures cannot look like recovery.
- [ ] Keep notifications deduplicated and include the manual MT5 service action when evidence is stale.
- [ ] Run focused tests.

### Task 6: Verification and safe publication

**Files:**
- Modify: `README.md` only if operator instructions changed.

- [ ] Run all tests.
- [ ] Run static compilation of every modified Python file.
- [ ] Re-run the causal audit against the captured Thursday events and compare blocker classes.
- [ ] Inspect the complete diff for strategy drift; assert no entry, lot, TP, or base-SL configuration changed.
- [ ] Commit and push only after verification.
- [ ] Confirm the VM has zero open positions before activation.
- [ ] Fast-forward the VM, verify commit and clean status, and confirm one watcher plus one bot process.
