# Gold 555 Demo Trial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the frozen Gold Signals `555124a2...` policy for new Canal 2 NOW signals on the verified EUR demo account while preserving exact recovery, observability and one-switch rollback to c490.

**Architecture:** Keep c490 and 555 as separate immutable policy identities. A durable pre-entry coordinator consumes new XAUUSD ticks until the adverse-move/reversal trigger confirms or expires; after the first real fill, the existing lifecycle monitor owns the adverse ladder, fixed per-leg targets, broker-side trailing stops, basket profit lock and conditional time exit. Every decision is journaled with policy fingerprint and enough inputs to compare live behavior with the frozen replay.

**Tech Stack:** Python 3.11, asyncio, MetaTrader5 Python API, JSONL journal, durable pending-action queue, pytest.

---

### Task 1: Freeze the pure 555 policy contract

**Files:**
- Create: `gold_555_live_candidate.py`
- Create: `tests/test_gold_555_live_candidate.py`

- [ ] **Step 1: Write failing tests for policy identity and risk contract**

```python
def test_policy_matches_frozen_candidate():
    policy = Gold555Policy()
    assert CANDIDATE_ID == "gold_now_555_v1"
    assert CANDIDATE_FINGERPRINT == "555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0"
    assert policy.entry_volumes == (0.04, 0.03, 0.03, 0.03, 0.03)
    assert policy.max_signal_volume == pytest.approx(0.16)
    assert policy.entry_expiry_minutes == 30
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m pytest tests/test_gold_555_live_candidate.py -q`

Expected: collection fails because `gold_555_live_candidate` does not exist.

- [ ] **Step 3: Implement the immutable policy and account gate**

```python
@dataclass(frozen=True)
class Gold555Policy:
    entry_adverse: float = 1.0
    entry_reversal: float = 1.5
    entry_expiry_minutes: int = 30
    entry_volumes: tuple[float, ...] = (0.04, 0.03, 0.03, 0.03, 0.03)
    ladder_step: float = 1.5
    target_steps: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 2.5)
    trailing_distance: float = 30.0
    profit_arm_eur: float = 30.0
    profit_giveback_eur: float = 1.0
    non_negative_exit_minutes: int = 180

    @property
    def max_signal_volume(self) -> float:
        return sum(self.entry_volumes)
```

- [ ] **Step 4: Add RED/GREEN tests for BUY/SELL targets, ladder levels, monotonic trailing stops, basket guard and demo-EUR rejection**

Required cases:
- BUY and SELL use the correct quote side and direction.
- Targets are based on each real fill, not requested price.
- A trailing stop can tighten but never loosen.
- Profit lock arms at 30 EUR and closes after a 1 EUR giveback.
- At 180 minutes, only a non-negative basket closes.
- Live account or non-EUR account raises `Gold555AccountError`.

- [ ] **Step 5: Run the focused suite and commit**

Run: `python -m pytest tests/test_gold_555_live_candidate.py -q`

Expected: all focused tests pass.

Commit: `feat: define frozen Gold 555 policy`

### Task 2: Add a reversible live-policy selector

**Files:**
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `README.md`
- Create: `tests/test_gold_live_policy_selector.py`

- [ ] **Step 1: Write failing selector tests**

```python
@pytest.mark.parametrize("raw, expected", [
    ("c490", "c490"),
    ("555", "555"),
    ("legacy", "legacy"),
])
def test_gold_policy_selector_accepts_supported_values(raw, expected):
    assert normalize_gold_now_policy(raw) == expected

def test_gold_policy_selector_rejects_unknown_values():
    with pytest.raises(ValueError):
        normalize_gold_now_policy("experimental")
```

- [ ] **Step 2: Run the selector tests and verify RED**

Run: `python -m pytest tests/test_gold_live_policy_selector.py -q`

- [ ] **Step 3: Implement `GOLD_NOW_LIVE_POLICY` with c490 default**

The selector must expose `c490`, `555` and `legacy`, retain the old c490 flag only as a compatibility input, and fail startup on an unknown value. Document that the VM trial sets `GOLD_NOW_LIVE_POLICY=555`.

- [ ] **Step 4: Run selector plus existing c490 tests and commit**

Run: `python -m pytest tests/test_gold_live_policy_selector.py tests/test_gold_live_candidate.py tests/test_gold_live_listener.py -q`

Commit: `feat: select Gold NOW live policy explicitly`

### Task 3: Build the durable pre-entry state machine

**Files:**
- Create: `gold_555_entry_watch.py`
- Create: `tests/test_gold_555_entry_watch.py`

- [ ] **Step 1: Write failing state-machine tests**

```python
def test_buy_waits_for_adverse_move_then_reversal():
    state = EntryWatch.new("BUY", reference=4300.0, observed_at=NOW)
    assert state.on_quote(bid=4299.2, ask=4299.4, now=NOW).action == "wait"
    assert state.on_quote(bid=4298.7, ask=4298.9, now=NOW).action == "armed"
    assert state.on_quote(bid=4299.9, ask=4300.4, now=NOW).action == "confirm"

def test_sell_tracks_a_new_adverse_extreme_before_confirming():
    state = EntryWatch.new("SELL", reference=4300.0, observed_at=NOW)
    state.on_quote(bid=4301.2, ask=4301.4, now=NOW)
    state.on_quote(bid=4302.0, ask=4302.2, now=NOW)
    assert state.on_quote(bid=4300.5, ask=4300.7, now=NOW).action == "confirm"
```

- [ ] **Step 2: Verify RED, then implement deterministic transitions**

The state must persist direction, reference quote, running adverse extreme, armed status, original observation time, expiry and terminal status. Quotes with repeated MT5 tick timestamps cannot advance twice.

- [ ] **Step 3: Add expiry, duplicate-confirmation and JSON round-trip tests**

Required assertions:
- expiry is anchored to original Telegram observation time;
- expired watches never confirm;
- a confirmed watch cannot confirm a second time;
- `to_dict()`/`from_dict()` preserve all state exactly.

- [ ] **Step 4: Run and commit**

Run: `python -m pytest tests/test_gold_555_entry_watch.py -q`

Commit: `feat: add durable Gold 555 entry watch`

### Task 4: Route Canal 2 NOW signals into the 555 watcher

**Files:**
- Modify: `listener.py`
- Modify: `main.py`
- Modify: `state.py`
- Create: `tests/test_gold_555_listener.py`

- [ ] **Step 1: Write failing listener tests**

Test that a `telegram_now` intent under policy `555`:
- is claimed exactly once;
- sends no MT5 order immediately;
- journals `gold_555_entry_watch_started` with fingerprint and source timestamp;
- opens 0.04 only after confirmation;
- journals expiry as an unfilled signal;
- leaves zones and c490 baskets unchanged.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_gold_555_listener.py -q`

- [ ] **Step 3: Implement the watcher registry and background tick loop**

Use a registry keyed by `(channel, message_id)`, an async lock per watch and a single XAUUSD quote loop. Confirmation must remove/finalize the watch before the order call so duplicate Telegram delivery or a repeated tick cannot create a second first leg.

- [ ] **Step 4: Implement journal recovery before Telegram startup**

Replay `gold_555_entry_watch_started`, state-transition and terminal events. Restore only unexpired non-terminal watches whose message identity has no open/reconciled basket.

- [ ] **Step 5: Run listener/startup tests and commit**

Run: `python -m pytest tests/test_gold_555_listener.py tests/test_canal2_entry_intent.py tests/test_main_startup.py -q`

Commit: `feat: route Gold NOW signals through 555 confirmation`

### Task 5: Execute and supervise the five-leg 555 basket

**Files:**
- Modify: `position_lifecycle_monitor.py`
- Modify: `pending_actions.py`
- Modify: `listener.py`
- Create: `tests/test_gold_555_monitor.py`

- [ ] **Step 1: Write failing execution tests**

Cover:
- first real fill anchors the four adverse levels;
- each level opens at most once and uses 0.03;
- a crossed sequence opens all newly crossed ranks in order;
- each real fill gets its own target rank;
- initial SL and subsequent trailing SL are queued durably;
- a failed SL/TP modification remains pending.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_gold_555_monitor.py -q`

- [ ] **Step 3: Generalize the existing candidate-leg tick path**

Keep Dubai behavior unchanged. For 555, derive entry levels from the first actual fill and use per-rank volume/target helpers. Persist filled indexes before returning from every successful order path.

- [ ] **Step 4: Implement trailing and basket exits on every unique MT5 tick**

For each open ticket, calculate a stop 30 USD behind the current executable quote and enqueue only a strictly tighter, broker-valid level. Then evaluate realized plus floating basket P/L for +30/+1 lock and the non-negative 180-minute exit.

- [ ] **Step 5: Run monitor, pending-action and regression tests; commit**

Run: `python -m pytest tests/test_gold_555_monitor.py tests/test_gold_live_monitor.py tests/test_dubai_live_monitor.py tests/test_pending_actions.py -q`

Commit: `feat: execute Gold 555 basket lifecycle`

### Task 6: Isolate provider management and restart recovery

**Files:**
- Modify: `listener.py`
- Modify: `executor.py`
- Modify: `main.py`
- Modify: `position_lifecycle_monitor.py`
- Create: `tests/test_gold_555_recovery.py`

- [ ] **Step 1: Write failing policy-isolation tests**

Required cases:
- explicit `CLOSE`, `CLOSE_ALL` and supported partial closes reach a 555 basket;
- provider BE/SL/TP mutations do not replace 555 levels;
- c490 retains its current suppression rules;
- legacy and zones remain untouched.

- [ ] **Step 2: Add a distinct MT5 comment marker and verify RED**

Use a compact marker that fits MT5 comment limits and cannot be parsed as c490. Recovery must identify strategy ID and leg rank from `c2_<msg>...` comments.

- [ ] **Step 3: Implement resync reconstruction**

Rebuild immutable policy identity, real fill prices, target ranks, current SLs, filled indexes, first-fill timestamp and basket guard state from MT5 plus journal evidence. A policy mismatch may block new entries but may not abandon open positions.

- [ ] **Step 4: Run recovery/resync tests and commit**

Run: `python -m pytest tests/test_gold_555_recovery.py tests/test_executor_resync.py tests/test_main_startup.py tests/test_signal_retraction.py -q`

Commit: `feat: recover Gold 555 baskets independently`

### Task 7: Make trial behavior visible and auditable

**Files:**
- Modify: `main.py`
- Modify: `listener.py`
- Modify: `position_lifecycle_monitor.py`
- Modify: `live_auditor.py`
- Create: `tests/test_gold_555_observability.py`

- [ ] **Step 1: Write failing contract/telemetry tests**

Assert startup emits `gold_now_555_v1`, full fingerprint, account gate, maximum volume and selector. Assert every watch transition, fill, target/SL decision, guard update, exit and error includes message ID, policy ID, fingerprint, broker tick time and decision inputs.

- [ ] **Step 2: Add one deduplicated human alert per actionable fault**

Alert on inability to place/maintain protection, prolonged exposure and recovery ambiguity. Do not alert on routine waiting, normal trailing updates or repeated retries.

- [ ] **Step 3: Run focused observability tests and commit**

Run: `python -m pytest tests/test_gold_555_observability.py tests/test_live_auditor.py tests/test_main_startup.py -q`

Commit: `feat: audit Gold 555 live trial decisions`

### Task 8: Full verification and guarded VM deployment

**Files:**
- Modify only if tests expose a defect.

- [ ] **Step 1: Run syntax and focused suites**

Run: `python -m compileall -q .`

Run: `python -m pytest tests/test_gold_555_live_candidate.py tests/test_gold_555_entry_watch.py tests/test_gold_555_listener.py tests/test_gold_555_monitor.py tests/test_gold_555_recovery.py tests/test_gold_555_observability.py -q`

- [ ] **Step 2: Run the complete repository suite**

Run: `python -m pytest -q`

Expected: zero failures.

- [ ] **Step 3: Inspect the exact diff and branch state**

Run: `git diff origin/main...HEAD --check`

Run: `git status --short --branch`

Expected: no whitespace errors and a clean worktree.

- [ ] **Step 4: Re-check the VM safety gate immediately before push**

Over SSH, verify the account remains demo/EUR, `positions_total()==0`, the VM repository is on clean `main`, and watcher/main are healthy. Do not push if any position is open.

- [ ] **Step 5: Push, configure and visibly restart**

Push the reviewed commits to `origin/main`, set `GOLD_NOW_LIVE_POLICY=555` on the VM without exposing secrets, and let the visible watcher fast-forward/restart. Do not manually kill MT5.

- [ ] **Step 6: Verify the deployed contract**

Confirm the VM commit equals `origin/main`, exactly one watcher and one bot process run, startup records `gold_now_555_v1` plus fingerprint, zero orders were created at startup, and the first eligible signal creates a durable pre-entry watch before any order.

- [ ] **Step 7: Record the two-day trial boundary**

Record trial start commit/time and baseline balance. Daily review must report fills, unfilled watches, maximum adverse excursion, maximum consecutive realized losses, slippage from replay, policy deviations and final P/L without extrapolating an edge from two days.
