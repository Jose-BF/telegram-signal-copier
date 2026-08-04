# Gold Signals Zone Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute complete Gold Signals zone plans on first touch, explicit activation and explicit re-entry while preserving the existing immediate-entry path and exact replay evidence.

**Architecture:** Add pure lifecycle helpers in a focused module, keep the event-sourced plan store in `listener.py`, and refactor the existing Canal 2 opening body into one intent-driven function shared by immediate and zone triggers. A single tick monitor evaluates all armed plans. Incremental analysis summarizes the new lifecycle events without rescanning the retained corpus.

**Tech Stack:** Python 3.14, asyncio, Telethon, MetaTrader5, pytest, append-only JSONL journal.

## Global Constraints

- Immediate `BUY/SELL NOW` behavior must remain compatible.
- Live modules must not import offline simulation modules.
- A Telegram identity may create at most one confirmed MT5 exposure generation.
- First-touch uses Ask for BUY and Bid for SELL.
- Legacy zone records must never become executable after an upgrade.
- No remote push may occur while production has open bot positions.
- All production changes use test-first red-green cycles.

---

### Task 1: Pure Zone Semantics

**Files:**
- Create: `canal2_zone_lifecycle.py`
- Modify: `parser.py`
- Test: `tests/test_canal2_zone_lifecycle.py`
- Test: `tests/test_parser.py`

**Interfaces:**
- Produces: `new_plan_record(...) -> dict`, `merge_plan_record(...) -> tuple[dict, list[str]]`, `classify_followup(text) -> list[str]`, `is_executable(plan) -> bool`, `touch_decision(plan, tick) -> dict | None`, `is_expired(plan, now_utc) -> bool`.
- Extends: `parse_canal2_zone_plan(text, inherited_direction=None) -> dict | None` with `tps`, `sl` and `has_open_runner`.

- [ ] **Step 1: Write failing parser tests using retained messages**

```python
def test_complete_zone_preserves_trade_levels():
    parsed = parse_canal2_zone_plan(
        "Gold Buy Zone\n4058 - 4053\nTargets\n4060\n4062\nOpen\nSL 4050"
    )
    assert parsed["direction"] == "BUY"
    assert parsed["zones"] == [[4053.0, 4058.0]]
    assert parsed["tps"] == [4060.0, 4062.0]
    assert parsed["sl"] == 4050.0
    assert parsed["has_open_runner"] is True

def test_session_map_inherits_bullish_direction():
    parsed = parse_canal2_zone_plan(
        "The zones are\n4073-4071\n4068-4067",
        inherited_direction="BUY",
    )
    assert parsed["zones"] == [[4071.0, 4073.0], [4067.0, 4068.0]]
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_parser.py -q`

Expected: failure because the parser lacks the new fields and inherited direction.

- [ ] **Step 3: Implement the parser extension and pure lifecycle helpers**

```python
def touch_decision(plan: dict, tick: dict) -> dict | None:
    side = "ask" if plan["direction"] == "BUY" else "bid"
    price = float(tick[side])
    low, high = plan["zones"][0]
    if low <= price <= high:
        return {"side": side, "price": price, "time_msc": tick.get("time_msc")}
    return None
```

`classify_followup` must recognize `APPROACHING`, `ACTIVATE`, `MISSED`,
`REARM`, `REENTRY`, `NO_REENTRY`, `INVALIDATE` and `EXTEND_VALIDITY` without
classifying progress text as an order.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_parser.py tests/test_canal2_zone_lifecycle.py -q`

- [ ] **Step 5: Commit the pure semantics**

```powershell
git add parser.py canal2_zone_lifecycle.py tests/test_parser.py tests/test_canal2_zone_lifecycle.py
git commit -m "feat: model Gold Signals zone lifecycle"
```

### Task 2: Durable Plan Store And Recursive Reply Identity

**Files:**
- Modify: `listener.py`
- Modify: `main.py`
- Test: `tests/test_channel_msg_detectors.py`
- Test: `tests/test_main_startup.py`

**Interfaces:**
- Consumes: pure lifecycle functions from Task 1.
- Produces: `_resolve_canal2_zone_plan(msg, reply_id)`, `_transition_canal2_zone_plan(...)`, versioned journal events, and schema-v2 restoration.

- [ ] **Step 1: Add failing tests for aliases, merged edits and restoration**

```python
async def test_reply_to_approaching_resolves_original_plan():
    await _process_canal2_new(zone_root)
    await _process_canal2_new(approaching_reply)
    await _process_canal2_new(still_valid_reply_to_approaching)
    assert listener._canal2_zone_plans[still_valid_reply_to_approaching.id] is (
        listener._canal2_zone_plans[zone_root.id]
    )

def test_restore_does_not_arm_legacy_observation_record(tmp_path):
    restored = restore_canal2_zone_plans_from_journal(legacy_events)
    assert restored == 0
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_channel_msg_detectors.py tests/test_main_startup.py -q`

- [ ] **Step 3: Merge edits and transitions idempotently**

Each follow-up ID becomes an alias. A full-plan `Active` reply merges into its
ancestor instead of creating a second root. Journal events include
`canal2_zone_plan_created`, `canal2_zone_plan_updated`,
`canal2_zone_plan_transition` and `canal2_zone_plan_alias_registered` with
`lifecycle_schema_version=2`.

- [ ] **Step 4: Restore only eligible unconsumed schema-v2 plans**

Restoration must replay transitions, aliases, trigger claims, expiry and entry
generation IDs. It must leave legacy `canal2_zone_plan_registered` rows
observation-only.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_channel_msg_detectors.py tests/test_main_startup.py -q`

- [ ] **Step 6: Commit the durable routing**

```powershell
git add listener.py main.py tests/test_channel_msg_detectors.py tests/test_main_startup.py
git commit -m "feat: persist Gold Signals zone threads"
```

### Task 3: One Canal 2 Opening Path

**Files:**
- Modify: `listener.py`
- Modify: `state.py`
- Test: `tests/test_channel_msg_detectors.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces: `_Canal2EntryIntent` and `_open_canal2_intent(intent, label)`.
- Preserves: `_process_canal2_new(msg)` as the immediate Telegram adapter.

- [ ] **Step 1: Add failing compatibility and zone-intent tests**

```python
async def test_now_and_zone_intents_use_same_market_opening_path():
    await _process_canal2_new(now_message)
    await _open_canal2_zone_generation(plan, trigger)
    assert [call.volume for call in mt5_calls] == [0.01, 0.01]
    assert all(call.magic == config.magic_for("canal2") for call in mt5_calls)

async def test_same_zone_trigger_cannot_open_twice():
    await asyncio.gather(trigger_plan(plan), trigger_plan(plan))
    assert len(primary_market_calls) == 1
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_channel_msg_detectors.py tests/test_state.py -q`

- [ ] **Step 3: Extract the existing opening body without behavior changes**

`_Canal2EntryIntent` carries identity message ID, source timestamp, trigger
timestamp/message ID, trigger kind, parsed levels, raw provider text, reply
identity and high-risk metadata. Immediate messages still enforce Telegram age
and duplicate-command checks; zone triggers use their fresh trigger timestamp.

- [ ] **Step 4: Add zone generation metadata to Signal and journal**

Record `entry_trigger_kind`, `provider_plan_message_id`,
`provider_trigger_message_id`, `provider_entry_generation` and trigger tick.
MT5 comments remain `c2_<identity>` so restart resync stays compatible.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_channel_msg_detectors.py tests/test_state.py -q`

- [ ] **Step 6: Commit the shared opening path**

```powershell
git add listener.py state.py tests/test_channel_msg_detectors.py tests/test_state.py
git commit -m "refactor: share Canal 2 entry execution"
```

### Task 4: First-Touch, Activation And Re-entry Execution

**Files:**
- Modify: `executor.py`
- Modify: `listener.py`
- Modify: `main.py`
- Modify: `config.py`
- Modify: `.env.example`
- Test: `tests/test_executor_anomalies.py`
- Test: `tests/test_channel_msg_detectors.py`
- Test: `tests/test_main_startup.py`

**Interfaces:**
- Extends: `executor.current_tick_safe()` with `time` and `time_msc`.
- Produces: `canal2_zone_touch_loop()` and `_open_canal2_zone_generation(...)`.

- [ ] **Step 1: Add failing tests for all trigger paths**

Cover an in-range first touch, a repeated identical tick, Active outside the
range, Active before the final SL edit, explicit re-entry, `Do not re-enter`,
an expired plan, MT5 failure followed by retry, and a multi-zone context map.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_executor_anomalies.py tests/test_channel_msg_detectors.py tests/test_main_startup.py -q`

- [ ] **Step 3: Implement the single-tick monitor**

The monitor reads one tick for all plans, skips repeated `time_msc`, expires
stale records, claims one generation atomically and calls the shared opening
path. A plan becomes consumed only after the first fill is confirmed.

- [ ] **Step 4: Wire explicit activation and re-entry**

Activation opens immediately or becomes `activation_pending` until levels are
complete. Re-entry uses the re-entry message ID as a new Signal identity.
Management replies route to the identified live generation; multiple ambiguous
generations notify instead of guessing.

- [ ] **Step 5: Start the monitor after MT5 resync and plan restoration**

`main.py` starts `canal2_zone_touch_loop()` before the Telegram disconnect wait
and records `canal2_zone_monitor_started`.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_executor_anomalies.py tests/test_channel_msg_detectors.py tests/test_main_startup.py -q`

- [ ] **Step 7: Commit live execution**

```powershell
git add executor.py listener.py main.py config.py .env.example tests/test_executor_anomalies.py tests/test_channel_msg_detectors.py tests/test_main_startup.py
git commit -m "feat: execute complete Gold Signals zones"
```

### Task 5: Mutually Exclusive Provider Alternatives

**Files:**
- Modify: `classifier.py`
- Modify: `listener.py`
- Test: `tests/test_classifier.py`
- Test: `tests/test_close_first_canal2.py`

**Interfaces:**
- Produces: one `CLOSE_PROFIT_OR_BE` semantic action for provider alternatives.
- Consumes: live `TradeContext.floating_pnl_total` to select one branch.

- [ ] **Step 1: Add a failing retained-message regression**

```python
async def test_close_profit_or_be_selects_only_be_when_basket_negative():
    actions = await classify_async(
        "+25 pips. Close overall profit or set breakeven", signal=signal
    )
    await _execute_actions(signal, actions, raw_text=message)
    assert close_all_calls == []
    assert be_ticket_calls == signal.all_filled_tickets
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_classifier.py tests/test_close_first_canal2.py -q`

- [ ] **Step 3: Implement one context-selected branch**

Positive P/L selects `CLOSE_ALL`; zero or negative P/L selects per-ticket BE.
Journal the provider alternative, observed P/L and selected branch. Never emit
both executable actions.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_classifier.py tests/test_close_first_canal2.py -q`

- [ ] **Step 5: Commit the alternative semantics**

```powershell
git add classifier.py listener.py tests/test_classifier.py tests/test_close_first_canal2.py
git commit -m "fix: choose one close-or-BE action"
```

### Task 6: Incremental Zone Analysis

**Files:**
- Modify: `log_analysis.py`
- Modify: `tools/analyze_new_logs.py`
- Test: `tests/test_log_analysis.py`

**Interfaces:**
- Extends: `summarize_events(events)` with `zone_lifecycle` counters.
- Preserves: cursor prefix verification and append-only incremental mode.

- [ ] **Step 1: Add a failing incremental-summary test**

```python
def test_incremental_summary_counts_zone_transitions_and_triggers():
    summary = summarize_events(zone_events)
    assert summary["zone_lifecycle"]["plans_created"] == 1
    assert summary["zone_lifecycle"]["entries_by_trigger"] == {
        "first_touch": 1,
        "activation": 1,
    }
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m pytest tests/test_log_analysis.py -q`

- [ ] **Step 3: Add compact counters without retaining raw text**

Count plans, transitions, expiry, aliases, trigger attempts, confirmed entry
generations, failures and unresolved lifecycle messages. Do not add another
full-file scan.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `python -m pytest tests/test_log_analysis.py -q`

- [ ] **Step 5: Commit incremental reporting**

```powershell
git add log_analysis.py tools/analyze_new_logs.py tests/test_log_analysis.py
git commit -m "feat: summarize zone lifecycle incrementally"
```

### Task 7: End-To-End Verification And Operational Documentation

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Test: all tests

**Interfaces:**
- Documents: live trigger rules, observability fields, restore behavior and
  commands for a daily incremental review.

- [ ] **Step 1: Run static repository checks**

Run: `git diff --check`

- [ ] **Step 2: Run focused end-to-end tests**

Run: `python -m pytest tests/test_parser.py tests/test_canal2_zone_lifecycle.py tests/test_channel_msg_detectors.py tests/test_classifier.py tests/test_close_first_canal2.py tests/test_log_analysis.py tests/test_main_startup.py -q`

- [ ] **Step 3: Run the complete suite**

Run: `python -m pytest -q`

Expected: all collected tests pass with zero failures.

- [ ] **Step 4: Document the operating contract**

Explain that formal complete zones execute in demo, context maps do not, and
the old NOW path remains unchanged. Include `python tools/analyze_new_logs.py`
as the normal daily summary command.

- [ ] **Step 5: Re-run the complete suite after documentation**

Run: `python -m pytest -q`

- [ ] **Step 6: Commit documentation**

```powershell
git add README.md AGENTS.md
git commit -m "docs: describe live zone execution"
```

- [ ] **Step 7: Inspect production before publication**

Confirm through SSH that the VM branch is clean and synchronized, exactly one
watcher/bot pair is running and no bot positions are open. If positions are
open, keep the verified branch local and do not push.
