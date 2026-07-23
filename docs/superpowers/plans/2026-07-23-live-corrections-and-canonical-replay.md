# Live Corrections and Canonical Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Telegram exposure creation idempotent, keep code updates from restarting live positions, attribute confirmed MT5 level changes correctly, and expose observed-versus-canonical execution evidence without blocking complete days.

**Architecture:** Runtime safety is enforced at the boundary that creates exposure and at the watcher boundary that replaces code. MT5 confirmations remain ticket-specific. Offline catalog construction derives deterministic execution batches from immutable journal events and publishes canonical corrections while preserving the observed replay unchanged.

**Tech Stack:** Python 3.11, asyncio, Telethon, MetaTrader5 Python API, pytest, JSON/JSONL runtime evidence.

---

## File Map

- Create `entry_execution_gate.py`: bounded, channel-agnostic in-memory exposure claim state.
- Modify `listener.py`: use the gate for Canal 2 recovery/new delivery and emit explicit reentry review evidence.
- Modify `classifier.py`: deterministic recognition of explicit additional-entry statements.
- Modify `interpretation_firewall.py`: preserve the provider entry price through review normalization.
- Modify `main.py`: publish bot-managed position exposure in the runtime heartbeat.
- Modify `tools/run_bot_watch.py`: defer code activation while heartbeat reports live exposure.
- Modify `live_auditor.py`: use recently confirmed ticket-level MT5 modifications.
- Modify `pending_actions.py`: retain a short snapshot of successful ticket-level modifications.
- Modify `provider_signal_catalog.py`: derive observed execution batches and canonical duplicate corrections.
- Modify focused test modules under `tests/`.

### Task 1: Unified Exposure Claim Gate

**Files:**
- Create: `entry_execution_gate.py`
- Modify: `listener.py`
- Test: `tests/test_entry_execution_gate.py`
- Test: `tests/test_channel_msg_detectors.py`

- [ ] **Step 1: Write failing unit tests for the gate**

Cover `claim`, concurrent rejection, permanent `commit`, retryable `release`,
bounded eviction, and channel isolation:

```python
gate = EntryExecutionGate(max_committed=2)
assert gate.claim("canal2", 266) is True
assert gate.claim("canal2", 266) is False
gate.commit("canal2", 266)
gate.release("canal2", 266)
assert gate.claim("canal2", 266) is False
assert gate.claim("canal1", 266) is True
```

- [ ] **Step 2: Run the gate tests and verify the module is missing**

Run: `pytest -q tests/test_entry_execution_gate.py`

Expected: FAIL during import because `entry_execution_gate.py` does not exist.

- [ ] **Step 3: Implement the bounded gate**

Implement `EntryExecutionGate` with:

```python
claim(channel, message_id) -> bool
commit(channel, message_id) -> None
release(channel, message_id) -> None
in_progress(channel, message_id) -> bool
committed(channel, message_id) -> bool
reset() -> None
```

`release` removes only an `opening` claim. `commit` is retained in FIFO order
and cannot be undone by a late failure path.

- [ ] **Step 4: Write the edit-first integration regression**

In `tests/test_channel_msg_detectors.py`, deliver the same Canal 2 message as:

1. fresh orphan edit;
2. completed market open;
3. later `poll_new`.

Assert exactly one call to `executor.open_market_with_fill`, one set of
scale-out legs, and a `canal2_entry_open_already_claimed` event for the second
delivery.

- [ ] **Step 5: Integrate the gate into Canal 2**

Replace `_canal2_opening_msg_ids` as the source of exposure authority. Claim
immediately before the first MT5 opening request. Release only when no position
was created. Commit as soon as the primary position is attached to `state`.

The orphan edit recovery path must also mark the message as seen by the new
message dispatcher, but correctness must rely on the exposure gate rather than
that optimization.

- [ ] **Step 6: Run focused listener tests**

Run:

```powershell
pytest -q tests/test_entry_execution_gate.py tests/test_channel_msg_detectors.py
```

Expected: PASS.

### Task 2: Rare Explicit Additional-Entry Review

**Files:**
- Modify: `classifier.py`
- Modify: `interpretation_firewall.py`
- Modify: `listener.py`
- Test: `tests/test_classifier.py`
- Test: `tests/test_interpretation_firewall.py`
- Test: `tests/test_telegram_perception.py`

- [ ] **Step 1: Write failing semantic tests**

Add deterministic cases:

```python
"I put more sell on 4055.00"
"I added more buys at 4032"
```

They must normalize to `REENTRY_SIGNAL`, preserve `price`, require human review,
and never execute an MT5 action. Conditional text such as `you can reenter if
BE was hit` must remain conditional/non-executable.

- [ ] **Step 2: Run the focused tests**

Run:

```powershell
pytest -q tests/test_classifier.py tests/test_interpretation_firewall.py tests/test_telegram_perception.py
```

Expected: at least the explicit-add tests FAIL because the current classifier
can return `MARKET_COMMENTARY`.

- [ ] **Step 3: Add narrow deterministic recognition**

Before Gemini, match only first-person completed/additive statements containing
direction and an absolute XAUUSD price. Return:

```python
{
    "action": "REENTRY_SIGNAL",
    "price": 4055.0,
    "confidence": 0.98,
    "_reason": "explicit_provider_additional_entry",
}
```

Do not match future, optional, or conditional wording.

- [ ] **Step 4: Improve the review alert**

For `REENTRY_SIGNAL`, show the provider price, current executable price and the
signed distance. State clearly that the bot made no change. Emit a structured
`explicit_additional_entry_review` event containing provider, direction,
provider price, current price, signal ID and source text hash.

- [ ] **Step 5: Run semantic and notification tests**

Run the same focused test command. Expected: PASS with zero executor calls.

### Task 3: Exposure-Aware Code Activation

**Files:**
- Modify: `main.py`
- Modify: `tools/run_bot_watch.py`
- Test: `tests/test_connection_monitors.py`
- Test: `tests/test_run_bot_watch.py`

- [ ] **Step 1: Write failing heartbeat tests**

Verify `_write_runtime_heartbeat` writes:

```json
{
  "schema_version": 2,
  "pid": 123,
  "open_signals": 1,
  "open_bot_positions": 4,
  "exposure_state": "open"
}
```

Mock `MetaTrader5.positions_get()` and count only the two configured bot magic
numbers. A failed MT5 query must produce `exposure_state="unknown"`, never
`"flat"`.

- [ ] **Step 2: Write failing watcher-defer tests**

Test the pure decision helper with fresh heartbeat states:

```python
assert should_defer_code_update({"exposure_state": "open"}) is True
assert should_defer_code_update({"exposure_state": "unknown"}) is True
assert should_defer_code_update({"exposure_state": "flat"}) is False
```

Also verify a data-only update remains restart-free.

- [ ] **Step 3: Publish exposure in the heartbeat**

Extend the existing atomic heartbeat write. Count actual MT5 positions by magic
and include the internal open-signal count for diagnosis. Keep heartbeat writes
best-effort and non-blocking to the Telegram handlers.

- [ ] **Step 4: Defer activation in the watcher**

When a code update is detected:

- retain the running process when exposure is `open` or `unknown`;
- record the remote revision in `runtime_update_pending.json`;
- print one concise pending-update notice per revision;
- keep fetching and publishing telemetry normally;
- activate the latest remote revision once exposure is confirmed `flat`.

Do not move local Git refs until activation is allowed.

- [ ] **Step 5: Run watcher and heartbeat tests**

Run:

```powershell
pytest -q tests/test_connection_monitors.py tests/test_run_bot_watch.py
```

Expected: PASS.

### Task 4: Ticket-Exact Confirmed Level Attribution

**Files:**
- Modify: `pending_actions.py`
- Modify: `live_auditor.py`
- Test: `tests/test_pending_actions.py`
- Test: `tests/test_live_auditor.py`

- [ ] **Step 1: Write the failing BE regression**

Create two open tickets with distinct TPs. Confirm BE modifications, remove the
active queue entries, then run the auditor one second later. Assert:

- no `mt5_level_change_unattributed`;
- each ticket retains its own TP;
- a genuinely manual later TP change still emits one warning.

- [ ] **Step 2: Run focused tests**

Run:

```powershell
pytest -q tests/test_pending_actions.py tests/test_live_auditor.py
```

Expected: the new post-confirmation test FAILS.

- [ ] **Step 3: Retain recent confirmed modifications**

After a successful `MODIFY_SLTP`, read the real post-action position snapshot,
update `Signal.sl_by_ticket` and `Signal.tp_by_ticket` from actual MT5 values,
and retain a bounded, expiring confirmation record:

```python
{
    "state": "confirmed_recent",
    "sig_id": "canal1_123",
    "ticket": 456,
    "new_sl": 4055.2,
    "new_tp": 4041.0,
    "confirmed_at": 1784820000.0,
}
```

- [ ] **Step 4: Teach the auditor to consume recent confirmations**

Recent confirmations may explain level changes but must not participate in
`pending_action_stuck`. Match by ticket and actual SL/TP fields. Expire them
after a short window longer than one auditor cycle.

- [ ] **Step 5: Run focused tests**

Run the same focused command. Expected: PASS.

### Task 5: Observed Execution Batches and Canonical Corrections

**Files:**
- Modify: `provider_signal_catalog.py`
- Modify: `provider_trade_spec.py`
- Test: `tests/test_provider_signal_catalog.py`
- Test: `tests/test_provider_trade_spec.py`
- Test: `tests/test_strategy_farm.py`

- [ ] **Step 1: Write failing catalog tests**

Build synthetic immutable events with two `signal_received` boundaries and two
five-ticket fill groups under the same signal ID. Assert:

```python
signal["execution_count"] == 2
signal["canonical_execution_count"] == 1
len(signal["execution_batches"]) == 2
signal["canonical_corrections"][0]["reason"] == (
    "duplicate_delivery_execution"
)
```

Also test one normal batch, two distinct formal signals, and older evidence
without `signal_received`.

- [ ] **Step 2: Run the catalog tests**

Run:

```powershell
pytest -q tests/test_provider_signal_catalog.py tests/test_provider_trade_spec.py tests/test_strategy_farm.py
```

Expected: FAIL because the catalog currently counts unique signal IDs rather
than exposure batches.

- [ ] **Step 3: Derive deterministic execution batches**

Scan events causally. Start a batch at `signal_received`; attach
`market_filled`, `market_b_filled` and `scale_out_leg_filled` tickets until the
next boundary. For legacy evidence without a boundary, create one inferred
batch. Give every batch a deterministic ID derived from signal ID, ordinal,
first fill timestamp and ticket set.

- [ ] **Step 4: Publish canonical correction metadata**

Preserve all batches as observed truth. For multiple batches under one
Telegram-root signal, keep the first in `canonical_execution_batch_ids` and
describe every excluded batch in `canonical_corrections` with its ticket set
and evidence timestamps. Increment the provider catalog schema version and keep
the previous `execution_sig_ids` field for compatibility. Do not mutate
`replay_trades.jsonl`.

- [ ] **Step 5: Keep the strategy farm provider-first**

Expose canonical execution metadata through `ProviderTradeSpec`, while keeping
price-path simulation based on one provider intent. Validate that duplicate
observed execution does not multiply a provider signal in policy results.

- [ ] **Step 6: Run catalog and strategy tests**

Run the same focused command. Expected: PASS.

### Task 6: Full Verification and July 23 Rebuild

**Files:**
- Modify generated canonical artifacts only through their existing builders.

- [ ] **Step 1: Run the complete test suite**

Run: `pytest -q`

Expected: all tests pass; the existing single environment-dependent skip may
remain.

- [ ] **Step 2: Check repository integrity**

Run:

```powershell
git diff --check
git status -sb
```

Expected: no whitespace errors and only intended source, test, documentation
and regenerated canonical artifacts.

- [ ] **Step 3: Rebuild the retained evidence locally**

Materialize current telemetry using the existing tooling, regenerate the
ledger, observed replay, provider catalog and readiness report, then run the
tick validator for the retained July window.

Expected:

- observed replay still contains every real ticket;
- the duplicated exposure is visible as two observed batches;
- provider-first canonical simulation uses one intended block;
- the exceptional additional-entry message is visible as review evidence;
- unaffected signals remain eligible independently.

- [ ] **Step 4: Report deployment state**

State exact commit hashes and distinguish:

1. local implementation;
2. GitHub push;
3. VM deployment.

Do not push, pull, restart or deploy until the user explicitly confirms it is
safe to do so.
