# Causal Action Lineage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox items.

**Goal:** Make every Telegram-triggered or internal MT5 action traceable from the exact message revision and bot decision through every broker attempt and result, without changing live trading decisions, order payloads, retry timing, or MT5 call counts.

**Architecture:** Add one dependency-free causal identity module and enrich the existing append-only journal envelope. Bind a message-revision context only while its Telegram handler runs. Persist one `action_id` per pending intent and create a new `attempt_id` only when the existing code is about to call MT5. Capture attempt evidence inside the executor from objects it already reads, so recording adds no broker round trips.

**Tech Stack:** Python 3.11 production runtime, `contextvars`, `uuid`, `hashlib`, existing JSONL journal, existing MetaTrader5 Python API, pytest.

**Global Constraints:**

- Do not alter signal interpretation, lot sizing, order count, SL/TP calculation, retry policy, queue timing, or MT5 request payloads.
- Do not add `positions_get`, `orders_get`, `symbol_info_tick`, `symbol_info`, or `account_info` calls to the live execution path.
- Logging remains best-effort, asynchronous, append-only, and unable to block order execution.
- Existing event names and fields remain valid; new envelope and lineage fields are additive.
- Old version-1 pending-action spool files must restore successfully.
- No production push, pull, restart, or VM action belongs to this plan.

---

## Plan 1 of 4: Causal identity and runtime journal evidence

This plan implements rollout Phase 1 from
`docs/superpowers/specs/2026-07-26-certified-counterfactual-portfolio-replay-design.md`.
Passive MQL5 observation, Telegram media archival, and the portfolio replay
scheduler are intentionally separate follow-up plans.

### Task 1: Add dependency-free causal identity primitives

**Files:**

- Create: `causal_trace.py`
- Create: `tests/test_causal_trace.py`

- [ ] **Step 1: Write failing tests for deterministic message revisions**

```python
def test_message_revision_id_is_stable_and_content_bound():
    first = message_revision_id(
        chat_id=-1003908582492,
        message_id=380,
        revision_token="2026-07-23T15:30:25+00:00",
        text_sha1="a" * 40,
        media_sha256=None,
    )
    same = message_revision_id(
        chat_id=-1003908582492,
        message_id=380,
        revision_token="2026-07-23T15:30:25+00:00",
        text_sha1="a" * 40,
        media_sha256=None,
    )
    edited = message_revision_id(
        chat_id=-1003908582492,
        message_id=380,
        revision_token="2026-07-23T15:31:00+00:00",
        text_sha1="b" * 40,
        media_sha256=None,
    )
    assert first == same
    assert first.startswith("msgrev_")
    assert edited != first
```

- [ ] **Step 2: Write failing tests for scoped context and unique IDs**

```python
def test_bound_context_is_visible_then_resets():
    assert current_fields() == {}
    with bind_message_revision("msgrev_a", decision_id="decision_b"):
        assert current_fields() == {
            "message_revision_id": "msgrev_a",
            "decision_id": "decision_b",
        }
    assert current_fields() == {}


def test_runtime_ids_are_prefixed_and_unique():
    assert new_action_id() != new_action_id()
    assert new_attempt_id() != new_attempt_id()
```

- [ ] **Step 3: Run the focused test and confirm it fails**

Run:

```text
pytest -q tests/test_causal_trace.py
```

Expected: collection fails because `causal_trace` does not exist.

- [ ] **Step 4: Implement the minimal identity module**

Implement:

```python
@dataclass(frozen=True)
class CausalContext:
    message_revision_id: str | None = None
    decision_id: str | None = None


def message_revision_id(
    *,
    chat_id: int,
    message_id: int,
    revision_token: str,
    text_sha1: str | None,
    media_sha256: str | None,
) -> str:
    payload = {
        "chat_id": int(chat_id),
        "message_id": int(message_id),
        "revision_token": str(revision_token),
        "text_sha1": text_sha1,
        "media_sha256": media_sha256,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"msgrev_{hashlib.sha256(canonical).hexdigest()}"
```

Use `uuid.uuid4().hex` for runtime-only `decision_`, `action_`, `attempt_`,
`event_`, and `session_` identifiers. Use a `ContextVar` plus a context
manager that always resets its token in `finally`.

- [ ] **Step 5: Run focused tests**

Run:

```text
pytest -q tests/test_causal_trace.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```text
git add causal_trace.py tests/test_causal_trace.py
git commit -m "feat: add causal trace identities"
```

### Task 2: Enrich every journal event without breaking old consumers

**Files:**

- Modify: `journal.py`
- Modify: `tests/test_journal.py`

- [ ] **Step 1: Add failing envelope tests**

Add tests asserting that two emitted events retain the existing `ts`, `sig`,
and `ev` fields and also contain:

```python
{
    "schema_version": 2,
    "event_id": "event_...",
    "session_id": "session_...",
    "monotonic_ns": int,
    "code_commit": str | None,
    "payload_sha256": "a" * 64,
}
```

Also assert:

- event IDs differ;
- monotonic timestamps do not decrease;
- equal semantic payloads have equal `payload_sha256`;
- changing one payload value changes `payload_sha256`;
- a bound causal context is copied into the event;
- explicit causal fields are preserved;
- `event()` still returns before a held disk lock is released.

- [ ] **Step 2: Run the journal tests and confirm the new assertions fail**

Run:

```text
pytest -q tests/test_journal.py
```

Expected: failures only for the absent envelope fields.

- [ ] **Step 3: Implement one additive envelope**

At module import, create one process `session_id`. Read the verified code
commit from `BOT_WATCHER_VERIFIED_HEAD` at event creation time. Build the
semantic payload first:

```python
semantic = {"sig": signal_id, "ev": ev}
if is_test:
    semantic["test"] = True
semantic.update(fields)
for key, value in causal_trace.current_fields().items():
    semantic.setdefault(key, value)
```

Hash a canonical JSON representation of `semantic`. Then add the envelope:

```python
record = {
    "schema_version": 2,
    "event_id": causal_trace.new_event_id(),
    "session_id": PROCESS_SESSION_ID,
    "ts": _now_iso(),
    "monotonic_ns": time.monotonic_ns(),
    "code_commit": os.getenv("BOT_WATCHER_VERIFIED_HEAD"),
    "payload_sha256": canonical_payload_sha256(semantic),
    **semantic,
}
```

Keep serialization and queueing in the caller exactly as today. Do not move
disk I/O into the handler thread.

- [ ] **Step 4: Verify journal compatibility**

Run:

```text
pytest -q tests/test_journal.py tests/test_reconcile_mt5_ledger.py tests/test_replay_source_contract.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```text
git add journal.py tests/test_journal.py
git commit -m "feat: add immutable journal event envelopes"
```

### Task 3: Bind raw and processed Telegram events to one revision

**Files:**

- Modify: `listener.py`
- Modify: `tests/test_telegram_perception.py`
- Modify: `tests/test_listener_helpers.py`

- [ ] **Step 1: Add failing raw-revision tests**

Assert `_telegram_raw_payload()` includes a stable
`message_revision_id`. Assert:

- the same new message produces the same ID on duplicate delivery;
- a text edit produces a different ID;
- a media-only revision still has an ID;
- channel/chat identity is part of the hash.

- [ ] **Step 2: Add a failing dispatch-correlation test**

Dispatch one fake message through `_dispatch_telegram_message()` with
processing monkeypatched to emit a journal event. Assert `telegram_raw`,
the processing event, and `telegram_processed` share the same
`message_revision_id`, while processing and acknowledgement share one
`decision_id`. After dispatch, emit an unrelated event and assert the Telegram
context did not leak.

- [ ] **Step 3: Run focused tests and confirm failure**

Run:

```text
pytest -q tests/test_telegram_perception.py tests/test_listener_helpers.py
```

Expected: only the new identity assertions fail.

- [ ] **Step 4: Implement one revision helper and scoped dispatch binding**

Compute the revision from:

```text
chat_id + message_id + revision_token + text_sha1 + media_sha256
```

For this phase `media_sha256` is `None`; Phase 2 will replace it when the
immutable media archive exists. Add the revision ID explicitly to
`telegram_raw`. In `_dispatch_telegram_message()`, bind that revision and a
new `decision_id` around the existing handler call and
`telegram_processed` acknowledgement. Reset in `finally`.

Do not change deduplication keys or the order in which raw logging, durable
flush, processing, and acknowledgement occur.

- [ ] **Step 5: Run focused tests**

Run:

```text
pytest -q tests/test_telegram_perception.py tests/test_listener_helpers.py
```

Expected: all pass.

- [ ] **Step 6: Commit**

```text
git add listener.py tests/test_telegram_perception.py tests/test_listener_helpers.py
git commit -m "feat: trace Telegram message revisions"
```

### Task 4: Persist one logical ID for every pending action

**Files:**

- Modify: `pending_actions.py`
- Modify: `tests/test_pending_actions.py`

- [ ] **Step 1: Add failing queue-lineage tests**

Cover:

- a new `PendingAction` receives one `action_id`;
- it captures the current `message_revision_id` and `decision_id`;
- the IDs survive spool save and restore;
- version-1 spool payloads without IDs restore with generated IDs;
- retrying preserves `action_id`;
- coalescing keeps one queue action and records the incoming revision;
- queue snapshots expose `action_id`, `decision_id`, and
  `message_revision_id`;
- request, waiting, confirmed, superseded, failed, and position-snapshot
  events all include the action lineage.

- [ ] **Step 2: Run the queue tests and confirm failure**

Run:

```text
pytest -q tests/test_pending_actions.py
```

Expected: failures for missing lineage fields only.

- [ ] **Step 3: Add immutable action fields**

Extend `PendingAction` with:

```python
action_id: str = field(default_factory=causal_trace.new_action_id)
decision_id: str = field(default_factory=causal_trace.current_or_new_decision_id)
message_revision_id: str | None = field(
    default_factory=causal_trace.current_message_revision_id
)
```

Persist these fields in spool schema version 2. When restoring an old spool,
generate IDs but mark emitted restoration evidence with
`lineage_recovered_from_legacy_spool=True`.

Centralize:

```python
def _lineage_fields(action: PendingAction) -> dict:
    return {
        "action_id": action.action_id,
        "decision_id": action.decision_id,
        "message_revision_id": action.message_revision_id,
        "action_revision": action.revision,
    }
```

Use it in every existing queue event. Do not change queue comparisons,
coalescing, retry decisions, or timings.

- [ ] **Step 4: Verify queue behavior**

Run:

```text
pytest -q tests/test_pending_actions.py
```

Expected: all pass and existing mock call counts remain unchanged.

- [ ] **Step 5: Commit**

```text
git add pending_actions.py tests/test_pending_actions.py
git commit -m "feat: persist pending action lineage"
```

### Task 5: Record each actual MT5 attempt from already-read evidence

**Files:**

- Modify: `executor.py`
- Modify: `pending_actions.py`
- Create: `tests/test_executor_causal_evidence.py`
- Modify: `tests/test_pending_actions.py`

- [ ] **Step 1: Add failing per-attempt evidence tests**

For market open, SL/TP modification, close, and pending cancellation, assert
one actual `order_send` produces one event containing:

```python
{
    "ev": "mt5_action_attempt",
    "action_id": "action_...",
    "attempt_id": "attempt_...",
    "decision_id": "decision_...",
    "message_revision_id": "msgrev_..." or None,
    "operation": "OPEN_MARKET" | "MODIFY_SLTP" | "CLOSE_POSITION" | "CANCEL_PENDING",
    "attempt_started_utc": str,
    "attempt_finished_utc": str,
    "attempt_started_monotonic_ns": int,
    "attempt_finished_monotonic_ns": int,
    "broker_request_sent": True,
    "request": dict,
    "result": dict,
    "source_tick": {"time_msc": int, "bid": float, "ask": float},
    "position_before": dict | None,
    "symbol_contract": {
        "point": float,
        "digits": int,
        "trade_stops_level": int,
        "trade_freeze_level": int,
    },
}
```

Also cover:

- `order_send is None` records `broker_request_sent=True` and
  `last_error`;
- validation failure before `order_send` records
  `broker_request_sent=False`;
- two retries share `action_id` and have distinct `attempt_id`;
- a thrown MT5 exception records one failed attempt and is still handled by
  the existing retry path.

- [ ] **Step 2: Add no-extra-IPC regression tests**

For each operation, record mock call counts before this change and assert the
instrumented path uses exactly the same count for:

```text
positions_get
orders_get
symbol_info_tick
symbol_info
order_send
```

This is the hard guard against adding latency or changing broker state.

- [ ] **Step 3: Run focused tests and confirm failure**

Run:

```text
pytest -q tests/test_executor_causal_evidence.py tests/test_pending_actions.py
```

Expected: new evidence tests fail; existing behavior tests pass.

- [ ] **Step 4: Add optional trace input without changing public callers**

Allow the executor methods to receive an optional keyword-only trace mapping:

```python
def modify_sltp_rc(..., *, trace: dict | None = None) -> int:
def close_position_rc(..., *, trace: dict | None = None) -> int:
def cancel_pending_rc(..., *, trace: dict | None = None) -> int:
```

The queue creates `attempt_id` immediately before each executor invocation and
passes:

```python
{
    "sig_id": f"{channel}_{message_id}",
    "action_id": action.action_id,
    "attempt_id": causal_trace.new_attempt_id(),
    "decision_id": action.decision_id,
    "message_revision_id": action.message_revision_id,
    "action_revision": action.revision,
}
```

Market opens create one action and attempt trace at function entry from the
current causal context.

- [ ] **Step 5: Capture evidence from existing local variables**

Inside each executor method, serialize `position`, `order`, `tick`,
`symbol_info`, request, and result objects that the method already obtained.
Do not query them again. Emit one `mt5_action_attempt` event in a `finally`
path. The logging helper must catch every exception and must never alter the
executor return value or re-raise.

Record complete `MqlTradeResult` evidence including:

```text
retcode, deal, order, volume, price, bid, ask, comment,
request_id, retcode_external
```

When a field is absent in the installed MT5 binding, record `None`.

- [ ] **Step 6: Verify focused behavior and call counts**

Run:

```text
pytest -q tests/test_executor_causal_evidence.py tests/test_pending_actions.py tests/test_executor_anomalies.py tests/test_executor_resync.py
```

Expected: all pass with unchanged MT5 call counts.

- [ ] **Step 7: Commit**

```text
git add executor.py pending_actions.py tests/test_executor_causal_evidence.py tests/test_pending_actions.py
git commit -m "feat: record causal MT5 execution attempts"
```

### Task 6: Prove replay compatibility and close Phase 1

**Files:**

- Modify: `tests/test_replay_source_contract.py`
- Modify: `tests/test_reconcile_mt5_ledger.py`
- Create: `tools/audit_causal_lineage.py`
- Create: `tests/test_audit_causal_lineage.py`
- Modify: `README.md`

- [ ] **Step 1: Add failing lineage-audit tests**

The audit must classify every relevant row as:

- `complete`;
- `legacy_before_contract`;
- `missing_message_revision`;
- `missing_decision`;
- `missing_action`;
- `missing_attempt`;
- `orphan_attempt`;
- `duplicate_id`;
- `contradictory_link`.

Test a complete chain, each missing link, a duplicated event ID, one action
linked to two decisions, and an attempt linked to two actions. No row may be
dropped from totals.

- [ ] **Step 2: Implement a read-only audit**

Command:

```text
python tools/audit_causal_lineage.py --events data/trade_events.jsonl --since 2026-07-06 --until 2026-07-24
```

It writes:

```text
data/causal_lineage_audit.json
```

The artifact includes source SHA-256, selected row count, counts by status,
affected signal IDs, first/last timestamps, and a deterministic fingerprint.
It never edits source logs.

- [ ] **Step 3: Add enriched-event compatibility fixtures**

Feed schema-version-2 events containing all new envelope and lineage fields
through replay-source and reconcile tests. Assert existing outputs are
unchanged except where the new audit artifact is explicitly read.

- [ ] **Step 4: Run the full repository suite**

Run:

```text
pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Run deterministic double-audit**

Run the audit twice on the same frozen fixture and compare the output bytes.
Expected: identical SHA-256.

- [ ] **Step 6: Document operational meaning**

Add a short README section:

- tracing is passive and does not change trading;
- `complete` means the causal chain is present, not that a strategy is
  profitable;
- legacy rows remain visible;
- any missing link blocks exact counterfactual certification later.

- [ ] **Step 7: Commit**

```text
git add tools/audit_causal_lineage.py tests/test_audit_causal_lineage.py tests/test_replay_source_contract.py tests/test_reconcile_mt5_ledger.py README.md
git commit -m "feat: audit causal replay lineage"
```

---

## Final verification gate

- [ ] Focused tests for each task pass.
- [ ] Full `pytest -q` passes.
- [ ] Existing market-open and pending-action request dictionaries are byte-for-byte equal in regression fixtures.
- [ ] Existing mock MT5 IPC call counts are unchanged.
- [ ] No production data file changed.
- [ ] No generated `.ex5`, cache, log, replay output, or VM artifact is staged.
- [ ] `git diff origin/main --stat` contains only source, tests, and documentation expected by this phase.
- [ ] Work remains local until the user explicitly asks to push.

## Spec coverage review

This plan covers only:

- causal IDs;
- immutable event envelope;
- Telegram revision binding;
- action and retry lineage;
- per-attempt MT5 evidence;
- read-only completeness auditing.

It deliberately does not cover:

- passive `OnTradeTransaction` observer;
- Telegram media downloads and OCR;
- live tick-window archival;
- account-level portfolio scheduler;
- modeled entry/fill scenarios;
- strategy ranking or profitability claims.

Those are separate plans because each has an independent safety boundary and
must be verified without changing the live order path.
