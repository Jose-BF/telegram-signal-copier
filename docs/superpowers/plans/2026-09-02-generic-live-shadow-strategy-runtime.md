# Generic Live/Shadow Strategy Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one immutable strategy contract define live and shadow semantics and prevent any live component from finalizing a temporarily flat strategy that still has eligible entry intents.

**Architecture:** Introduce a pure strategy-contract registry and lifecycle gate, then route reconciler, monitor, auditor, listener finalization and startup recovery through typed terminal requests. Keep MT5 execution and historical shadow adapters separate, but require them to consume the same fingerprinted contract and publish unambiguous evidence roles.

**Tech Stack:** Python 3.11/3.14, dataclasses, asyncio, pytest, Telethon, MetaTrader5 adapter, existing JSONL journal.

---

## File Map

- `strategy_runtime_contract.py`: immutable strategy plan and registry adapters.
- `signal_lifecycle.py`: pure pending-intent and terminal-decision state machine.
- `state.py`: durable per-signal lifecycle fields and compatibility accessors.
- `listener.py`: typed terminal requests and one definitive finalization path.
- `position_lifecycle_monitor.py`: generic automatic-flat decision.
- `main.py`: reconciler and startup-recovery lifecycle integration.
- `live_auditor.py`: generic temporary-flat recognition.
- `strategy_shadow_catalog.py`: construct shadows from canonical contracts.
- `strategy_shadow_report.py`: strict evidence-role labels and ranking gates.
- `tests/fixtures/canal2_2320_lifecycle.json`: minimized real regression trace.
- `tests/test_strategy_runtime_contract.py`: fingerprint and adapter parity.
- `tests/test_signal_lifecycle.py`: pure lifecycle matrix.
- `tests/test_strategy_runtime_lifecycle_integration.py`: reconciler/monitor/auditor parity.
- `tests/test_strategy_shadow_report.py`: actual-evidence fail-closed checks.
- `tests/test_live_terminalization_static.py`: forbid direct live close mutations.

### Task 1: Characterize the real regression

**Files:**
- Create: `tests/fixtures/canal2_2320_lifecycle.json`
- Create: `tests/test_strategy_runtime_lifecycle_integration.py`

- [ ] **Step 1: Add a fixture containing the frozen 555 identity, first fill,
  first TP closure, remaining four intents and original expiry.**

```json
{
  "signal_id": "canal2_2320",
  "strategy_id": "gold_now_555_v1",
  "filled_leg_indexes": [0],
  "planned_leg_count": 5,
  "open_position_count": 0,
  "terminal_cause": "automatic_flat",
  "now_before_expiry": true
}
```

- [ ] **Step 2: Write an integration test asserting reconciler, monitor and
  auditor all return `keep_alive` for the fixture.**

```python
def test_canal2_2320_remains_alive_after_first_target():
    fixture = load_lifecycle_fixture("canal2_2320_lifecycle.json")
    decisions = evaluate_all_live_finalizers(fixture)
    assert {item.action for item in decisions} == {"keep_alive"}
    assert {item.reason for item in decisions} == {"eligible_entry_intents"}
```

- [ ] **Step 3: Run the test and verify RED.**

Run: `python -m pytest tests/test_strategy_runtime_lifecycle_integration.py -q`

Expected: FAIL because the independent reconciler still finalizes the signal.

- [ ] **Step 4: Commit the red regression fixture.**

Run: `git add tests/fixtures tests/test_strategy_runtime_lifecycle_integration.py && git commit -m "test: reproduce temporary-flat 555 finalization"`

### Task 2: Define the canonical strategy contract

**Files:**
- Create: `strategy_runtime_contract.py`
- Create: `tests/test_strategy_runtime_contract.py`
- Modify: `strategy_shadow_contracts.py`
- Modify: `strategy_shadow_catalog.py`

- [ ] **Step 1: Write failing tests for canonical fingerprints, pending-intent
  policy and live/shadow adapter equality.**

```python
def test_gold_555_live_and_shadow_compile_from_one_contract():
    contract = strategy_contract_by_id("gold_now_555_v1")
    assert contract.pending_entry_policy == "until_expiry"
    assert contract.to_live_plan().fingerprint == contract.fingerprint
    assert contract.to_shadow_policy(role="candidate").strategy_fingerprint == contract.fingerprint
```

- [ ] **Step 2: Run and verify RED.**

Run: `python -m pytest tests/test_strategy_runtime_contract.py -q`

Expected: FAIL because `strategy_runtime_contract` does not exist.

- [ ] **Step 3: Implement frozen `StrategyRuntimeContract`, `EntryLegContract`,
  `ProtectionContract`, `TerminalPolicy` and registry functions.**

The canonical payload excludes display role, includes every behavioural field,
uses sorted ASCII JSON and rejects duplicate IDs or mismatched supplied hashes.
Register Dubai balanced, Gold 555 and Gold c490 from their existing frozen
values. Shadow catalog construction becomes an adapter over this registry.

- [ ] **Step 4: Run focused parity suites and verify GREEN.**

Run: `python -m pytest tests/test_strategy_runtime_contract.py tests/test_strategy_shadow_catalog.py tests/test_strategy_shadow_parity.py -q`

Expected: all pass with unchanged frozen fingerprints.

- [ ] **Step 5: Commit.**

Run: `git add strategy_runtime_contract.py strategy_shadow_contracts.py strategy_shadow_catalog.py tests && git commit -m "feat: define canonical strategy runtime contracts"`

### Task 3: Add the pure lifecycle gate

**Files:**
- Create: `signal_lifecycle.py`
- Create: `tests/test_signal_lifecycle.py`
- Modify: `state.py`

- [ ] **Step 1: Write a failing lifecycle decision matrix.**

```python
@pytest.mark.parametrize("cause,expected", [
    ("automatic_flat", "keep_alive"),
    ("provider_close", "finalize"),
    ("strategy_stop", "finalize"),
    ("operator_close", "finalize"),
])
def test_pending_intents_block_only_automatic_finality(cause, expected):
    snapshot = lifecycle_snapshot(pending_eligible=4, open_positions=0)
    assert evaluate_terminal_request(snapshot, cause=cause).action == expected
```

- [ ] **Step 2: Run and verify RED.**

Run: `python -m pytest tests/test_signal_lifecycle.py -q`

Expected: FAIL because the lifecycle API does not exist.

- [ ] **Step 3: Implement typed causes and idempotent decisions.**

`automatic_flat` keeps a signal alive while an intent is eligible. Explicit
close, stop, time exit, retraction and operator causes cancel remaining intents
exactly once and permit finalization only when MT5 exposure is zero. Unknown
strategy contracts fail closed.

- [ ] **Step 4: Add serializable lifecycle fields to `Signal`.**

Store lifecycle state, terminal cause, settled/cancelled leg indexes and the
last decision evidence. Existing candidate fields remain readable during
migration and are normalized by one compatibility function.

- [ ] **Step 5: Run and verify GREEN.**

Run: `python -m pytest tests/test_signal_lifecycle.py tests/test_state.py -q`

- [ ] **Step 6: Commit.**

Run: `git add signal_lifecycle.py state.py tests/test_signal_lifecycle.py && git commit -m "feat: centralize strategy lifecycle decisions"`

### Task 4: Route every runtime finalizer through the gate

**Files:**
- Modify: `listener.py`
- Modify: `position_lifecycle_monitor.py`
- Modify: `main.py`
- Modify: `live_auditor.py`
- Modify: `pending_actions.py`
- Modify: `tests/test_strategy_runtime_lifecycle_integration.py`

- [ ] **Step 1: Expand the red integration test to exercise actual module
  entry points for automatic flat, explicit close and asynchronous MT5 close.**

- [ ] **Step 2: Run and verify the expected failures.**

Run: `python -m pytest tests/test_strategy_runtime_lifecycle_integration.py -q`

- [ ] **Step 3: Make `_finalize_signal` the only status transition to closed.**

Call the pure gate before journal finalization. Close requests set a typed cause
and enqueue MT5 work but remain live until zero positions are confirmed. A
blocked request records `lifecycle_finalization_deferred` with strategy ID,
fingerprint, remaining intents and cause.

- [ ] **Step 4: Replace strategy-specific temporary-flat helpers.**

Reconciler, monitor and auditor call the same gate. Pending-action completion
rechecks finality. Remove `_gold_555_waiting_for_remaining_legs` from monitor
and auditor after tests pass.

- [ ] **Step 5: Integrate startup recovery.**

Recovery reconstructs the contract and intent settlement from journal events.
It cannot finalize an unexpired flat plan; incomplete reconstruction emits a
blocker and leaves the signal recoverable.

- [ ] **Step 6: Run focused suites and verify GREEN.**

Run: `python -m pytest tests/test_strategy_runtime_lifecycle_integration.py tests/test_gold_555_monitor.py tests/test_gold_555_recovery.py tests/test_live_auditor.py tests/test_position_lifecycle_monitor.py -q`

- [ ] **Step 7: Commit.**

Run: `git add listener.py position_lifecycle_monitor.py main.py live_auditor.py pending_actions.py tests && git commit -m "fix: enforce one live strategy lifecycle gate"`

### Task 5: Enforce evidence labels and parity

**Files:**
- Modify: `strategy_shadow_settlement.py`
- Modify: `strategy_shadow_report.py`
- Modify: `strategy_shadow_parity.py`
- Modify: `tests/test_strategy_shadow_report.py`
- Modify: `tests/test_strategy_shadow_settlement.py`

- [ ] **Step 1: Write failing tests proving `live_control` is never emitted as
  actual money and missing broker deals suppress parity/ranking.**

```python
def test_missing_actual_mt5_blocks_parity_and_ranking():
    report = build_report(actual=None, live_logic_mirror=Decimal("1.72"))
    assert report["actual_mt5"] is None
    assert report["parity"] == "blocked"
    assert report["selection"] is None
    assert "actual_evidence_missing" in report["blockers"]
```

- [ ] **Step 2: Run and verify RED, then implement the three evidence roles:**
  `actual_mt5`, `live_logic_mirror`, `shadow_prediction`.

- [ ] **Step 3: Rebuild the `canal2_2320` report and require actual +1.72 EUR,
  mirror parity or an explicit blocker, and no false -310.18 actual label.**

- [ ] **Step 4: Run report and settlement suites and verify GREEN.**

Run: `python -m pytest tests/test_strategy_shadow_report.py tests/test_strategy_shadow_settlement.py tests/test_strategy_shadow_parity.py -q`

- [ ] **Step 5: Commit.**

Run: `git add strategy_shadow_* tests && git commit -m "fix: separate actual mirror and shadow evidence"`

### Task 6: Prevent lifecycle drift

**Files:**
- Create: `tests/test_live_terminalization_static.py`
- Modify: `AGENTS.md`
- Modify: `README.md`

- [ ] **Step 1: Write a static AST test that rejects assignments of live
  `Signal.status = "closed"` outside `signal_lifecycle.py` and the definitive
  finalizer.**

- [ ] **Step 2: Run it and verify RED against remaining direct assignments.**

- [ ] **Step 3: Remove or route every reported mutation through the service.**

- [ ] **Step 4: Document the contract, evidence labels and maintenance rule.**

- [ ] **Step 5: Run the static and complete suites.**

Run: `python -m pytest tests/test_live_terminalization_static.py -q`

Run: `python -m pytest -q`

- [ ] **Step 6: Commit.**

Run: `git add AGENTS.md README.md tests/test_live_terminalization_static.py && git commit -m "test: forbid live lifecycle bypasses"`

### Task 7: Operational acceptance

- [ ] **Step 1: Run deterministic regression twice and compare artifact hashes.**
- [ ] **Step 2: Run `python -m compileall -q .` and the full pytest suite.**
- [ ] **Step 3: Inspect `git diff --check` and confirm no secrets or generated
  research populations are staged.**
- [ ] **Step 4: Query VM MT5 and require zero open positions before deployment.**
- [ ] **Step 5: Fast-forward main, push once, verify VM commit and watcher health.**
- [ ] **Step 6: Send the exact commit, tests and deployment status by Telegram.**

