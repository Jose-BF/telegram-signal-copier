# Multichannel Strategy Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate three frozen Dubai Investing policies and three frozen Gold Signals policies prospectively on every eligible signal without sending, changing or closing any real MT5 order.

**Architecture:** Add a pure incremental engine behind a narrow runtime coordinator. The listener registers normalized entry and provider-management events, while the existing lifecycle loop gives the coordinator unique broker ticks only after live work has run; immutable checkpoints in the causal journal make restart recovery deterministic. A report joins the prospective results to actual MT5 evidence but refuses to rank candidates when control parity, ticks, money conversion or Telegram lineage are incomplete.

**Tech Stack:** Python 3.11, dataclasses, asyncio, MetaTrader5 read-only callbacks injected by `main.py`, SHA-256 canonical JSON fingerprints, JSONL causal journal, pytest.

---

## File Map

- `strategy_shadow_contracts.py`: immutable tick, signal, position, transition and state contracts with canonical serialization.
- `strategy_shadow_catalog.py`: the six frozen policies and validation of the active control for each channel.
- `strategy_shadow_engine.py`: side-effect-free state transitions for entries, ladders, targets, stops, management and basket guards.
- `strategy_shadow_runtime.py`: per-candidate isolation, tick deduplication, journal checkpoints and exact restart catch-up.
- `strategy_shadow_report.py`: completeness gates, control comparison and candidate/pairing summaries.
- `config.py`: one feature switch and bounded runtime/checkpoint settings.
- `listener.py`: registration of accepted formal signals and resolved management events.
- `main.py`: read-only MT5 tick/money/history adapters and lifecycle startup/shutdown.
- `tests/test_strategy_shadow_*.py`: focused contracts, engine, recovery, integration, safety and reporting tests.
- `README.md`: operator-facing description of shadow status and rollback switch.

### Task 1: Define immutable contracts and the frozen catalog

**Files:**
- Create: `strategy_shadow_contracts.py`
- Create: `strategy_shadow_catalog.py`
- Create: `tests/test_strategy_shadow_catalog.py`

- [ ] **Step 1: Write failing contract and catalog tests**

```python
def test_catalog_contains_three_frozen_candidates_per_channel():
    catalog = build_shadow_catalog()
    assert tuple(p.candidate_id for p in catalog["canal1"]) == (
        "dubai_balanced_v1",
        "dubai_frontloaded_30m_v1",
        "dubai_frontloaded_40m_v1",
    )
    assert tuple(p.candidate_id for p in catalog["canal2"]) == (
        "gold_now_555_v1",
        "gold_now_b210_v1",
        "gold_now_c490_v1",
    )

def test_execution_fingerprint_changes_when_live_only_contract_changes():
    policy = build_shadow_catalog()["canal2"][2]
    changed = dataclasses.replace(policy, hard_stop_eur_per_leg=21.0)
    assert changed.execution_fingerprint != policy.execution_fingerprint

def test_state_round_trip_preserves_hash():
    state = ShadowSignalState.new(
        signal_id="canal1_123",
        candidate_id="dubai_balanced_v1",
        channel="canal1",
        direction="BUY",
        registered_at_utc="2026-08-27T08:00:00+00:00",
    )
    assert ShadowSignalState.from_dict(state.to_dict()).state_hash == state.state_hash
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_strategy_shadow_catalog.py -q`

Expected: collection fails because the two shadow modules do not exist.

- [ ] **Step 3: Implement canonical contracts**

Define frozen `ShadowTick`, `ShadowManagementEvent`, `ShadowPosition` and `ShadowPolicy` dataclasses plus mutable `ShadowSignalState`. `ShadowTick.executable_price(direction, entry=...)` must use Ask for BUY entry/Bid for SELL entry and the opposite side for exits. `ShadowSignalState.to_dict()` must contain every field needed for recovery; `state_hash` must be SHA-256 over sorted compact JSON.

```python
def canonical_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()
```

- [ ] **Step 4: Implement the six frozen policies and validations**

Encode the exact parameters and strategy fingerprints from the approved design. Compute a separate execution fingerprint from all runtime semantics, including quote side, virtual-fill rule, money rounding, c490 immediate entry and c490 per-leg hard protection. Reject duplicate IDs, invalid directions, non-positive volumes and a live control missing from its channel.

- [ ] **Step 5: Run focused tests and commit**

Run: `python -m pytest tests/test_strategy_shadow_catalog.py -q`

Expected: all focused tests pass.

Commit: `feat: define frozen multichannel shadow catalog`

### Task 2: Implement causal entries and price-level exits

**Files:**
- Create: `strategy_shadow_engine.py`
- Create: `tests/test_strategy_shadow_engine.py`

- [ ] **Step 1: Write failing tests for all entry families**

```python
def test_dubai_market_and_adverse_ladder_fill_on_subsequent_ticks():
    state = new_state("dubai_balanced_v1", direction="BUY", registered_msc=100)
    first = advance(state, tick(msc=101, bid=4300.0, ask=4300.2))
    assert [(p.volume, p.entry_price) for p in first.state.positions] == [(0.01, 4300.2)]
    second = advance(first.state, tick(msc=102, bid=4296.0, ask=4296.2))
    assert [p.volume for p in second.state.positions] == [0.01, 0.04]

def test_c490_opens_five_virtual_legs_once_on_first_tick():
    result = advance(
        new_state("gold_now_c490_v1", direction="SELL", registered_msc=100),
        tick(msc=101, bid=4300.0, ask=4300.2),
    )
    assert [p.volume for p in result.state.positions] == [0.01] * 5
    assert advance(result.state, tick(msc=102, bid=4300.1, ask=4300.3)).transitions == ()

def test_555_requires_adverse_move_and_reversal_before_first_fill():
    state = new_state("gold_now_555_v1", direction="BUY", reference=4300.0)
    state = advance(state, tick(msc=101, bid=4298.7, ask=4298.9)).state
    result = advance(state, tick(msc=102, bid=4300.2, ask=4300.4))
    assert result.state.positions[0].volume == pytest.approx(0.04)
```

- [ ] **Step 2: Run the engine tests and verify RED**

Run: `python -m pytest tests/test_strategy_shadow_engine.py -q`

Expected: collection fails because `strategy_shadow_engine` does not exist.

- [ ] **Step 3: Implement pure entry transitions**

Expose `register_signal(policy, intent) -> ShadowSignalState` and `advance_tick(policy, state, tick) -> ShadowAdvance`. Ignore ticks at or before the registration cursor. Fill at the current executable quote, preserve leg rank and real virtual fill price, and return immutable `ShadowTransition` records only when state changes.

- [ ] **Step 4: Add failing tests for targets, trailing stops and c490 broker protection**

Required cases:
- 555 targets derive from each virtual fill and close on the correct exit quote;
- its 30-XAUUSD stop only tightens and a gap closes at the first observed executable quote;
- c490 calibrates each leg's hard stop from the injected negative EUR-per-move factor and never estimates when that factor is missing;
- c490 moves an eligible leg to break-even after a 12-XAUUSD favourable move;
- multiple levels crossed by one tick are processed in deterministic leg-rank order.

- [ ] **Step 5: Implement price exits and verify GREEN**

Use one deterministic event order per tick: pending provider close, existing protective stops, fixed targets, entry/ladder fills, break-even/trailing updates, then money guards. Closed positions retain fill, close quote, timestamp, reason and rounded EUR result.

Run: `python -m pytest tests/test_strategy_shadow_engine.py -q`

Expected: all engine tests pass.

Commit: `feat: add deterministic strategy shadow engine`

### Task 3: Add basket money guards and provider management semantics

**Files:**
- Modify: `strategy_shadow_engine.py`
- Modify: `tests/test_strategy_shadow_engine.py`

- [ ] **Step 1: Write failing money-evidence tests**

```python
def test_missing_money_factor_blocks_guard_without_guessing():
    result = advance(open_balanced_buy(), tick(msc=200, bid=4290.0, ask=4290.2,
                                               negative_factor=None))
    assert result.state.status == "open"
    assert "money_contract_missing" in result.state.evidence_blockers

def test_profit_lock_uses_realized_plus_floating_eur():
    armed = advance(open_balanced_buy(), profitable_tick(total_eur=10.0)).state
    closed = advance(armed, profitable_tick(total_eur=7.9, msc=202)).state
    assert closed.status == "closed"
    assert closed.exit_reason == "profit_giveback"
```

- [ ] **Step 2: Verify RED, then implement guard state**

Track realized, floating, maximum favourable and maximum adverse EUR. Apply per-policy basket loss, profit-arm/giveback, profit-only time exit, loss-only time exit and non-negative time exit. Round each leg to cents before basket aggregation so online and broker reconciliation use the same accounting boundary.

- [ ] **Step 3: Write failing provider-management tests**

Cover explicit close before entry, close after entry, duplicate management event, and non-close management. Dubai and b210 consume exact resolved management; 555 consumes explicit closes only; c490 ignores all provider management. A provider close becomes pending and fills on the first subsequent unique broker tick.

- [ ] **Step 4: Implement management transitions and verify**

Run: `python -m pytest tests/test_strategy_shadow_engine.py -q`

Expected: all focused tests pass, including missing-evidence blockers.

Commit: `feat: model shadow guards and provider actions`

### Task 4: Build isolated runtime, checkpoints and exact recovery

**Files:**
- Create: `strategy_shadow_runtime.py`
- Create: `tests/test_strategy_shadow_runtime.py`

- [ ] **Step 1: Write failing runtime isolation tests**

```python
async def test_candidate_exception_does_not_escape_or_disable_siblings():
    runtime = runtime_with_engine_that_fails_only("gold_now_b210_v1")
    await runtime.register_signal(gold_intent(msg_id=380))
    await runtime.process_tick(tick(msc=101))
    assert runtime.status("gold_now_b210_v1") == "disabled"
    assert runtime.status("gold_now_555_v1") == "running"
    assert runtime.status("gold_now_c490_v1") == "running"

async def test_runtime_registers_only_the_signal_channel():
    runtime = build_runtime()
    await runtime.register_signal(dubai_intent(msg_id=20700))
    assert runtime.active_candidate_ids() == set(DUBAI_IDS)
```

- [ ] **Step 2: Run runtime tests and verify RED**

Run: `python -m pytest tests/test_strategy_shadow_runtime.py -q`

Expected: collection fails because `strategy_shadow_runtime` does not exist.

- [ ] **Step 3: Implement coordinator and bounded journal writes**

Inject `journal_sink`, `current_tick_reader`, `tick_history_reader` and `money_factor_reader`. Keep one state per `(signal_id, candidate_id)`, deduplicate full tick identity `(time_msc, bid, ask, last, flags, volume_real)`, isolate exceptions per candidate, and emit only registration, transition, blocker, terminal, degradation and five-minute full checkpoint events.

- [ ] **Step 4: Write failing interruption/recovery tests**

Run the same fixture uninterrupted and with restarts before entry, after a ladder fill and immediately before exit. Require identical terminal state hash, positions, reasons and net EUR. Also require an `incomplete` result when broker history starts after the saved cursor or contains a gap that prevents exact continuity.

- [ ] **Step 5: Implement journal reconstruction and catch-up**

Recover from the newest valid full checkpoint plus later transitions. Replay historical ticks strictly after the stored cursor through startup before accepting a live tick. Verify candidate ID, both fingerprints and prior state hash on every transition; preserve partial evidence and disable only the corrupt candidate-signal pair on mismatch.

- [ ] **Step 6: Run focused tests and commit**

Run: `python -m pytest tests/test_strategy_shadow_runtime.py -q`

Expected: all runtime and recovery tests pass.

Commit: `feat: recover strategy shadows from causal evidence`

### Task 5: Integrate shadows without changing live execution

**Files:**
- Modify: `config.py`
- Modify: `listener.py`
- Modify: `main.py`
- Create: `tests/test_strategy_shadow_listener.py`
- Modify: `tests/test_public_safety.py`

- [ ] **Step 1: Write failing integration and static safety tests**

Required assertions:
- an accepted Dubai signal registers exactly the three Dubai candidates after the entry identity claim and before the real order call;
- an accepted Gold `telegram_now` signal registers exactly the three Gold candidates;
- Gold zone plans do not register shadows;
- resolved management is routed by original signal identity even when no live `Signal` object remains;
- the real order call occurs before shadow tick processing for a shared loop turn;
- exceptions from registration or processing never escape the listener or lifecycle loop;
- `strategy_shadow_contracts.py`, `strategy_shadow_catalog.py`, `strategy_shadow_engine.py`, `strategy_shadow_runtime.py` and `strategy_shadow_report.py` contain no imports of `executor`, `pending_actions` or `MetaTrader5`, and contain no `order_send` token.

- [ ] **Step 2: Run integration tests and verify RED**

Run: `python -m pytest tests/test_strategy_shadow_listener.py tests/test_public_safety.py -q`

- [ ] **Step 3: Add the feature switch and adapters**

Add `STRATEGY_SHADOW_ENABLED` defaulting to false, `STRATEGY_SHADOW_CHECKPOINT_SECONDS=300` and a bounded slowdown threshold. In `main.py`, create read-only adapters that return primitive tick/history values and derive positive/negative EUR movement factors through MT5 `order_calc_profit`; they may not submit or modify orders.

- [ ] **Step 4: Fan accepted entries and management into the runtime**

Call a no-throw facade after `_entry_open_claim` succeeds in both Dubai paths and in Gold NOW registration. Do not call it from zone-plan execution. Route resolved provider management with its source message/reply identity and observation timestamp. Start the shadow loop after live recovery and stop it during orderly shutdown.

- [ ] **Step 5: Verify integration and regressions**

Run: `python -m pytest tests/test_strategy_shadow_listener.py tests/test_public_safety.py tests/test_canal2_entry_intent.py tests/test_dubai_live_listener.py tests/test_gold_555_listener.py tests/test_main_startup.py -q`

Expected: all listed tests pass and no test observes an extra MT5 order call.

Commit: `feat: observe live signals with isolated strategy shadows`

### Task 6: Produce honest comparisons and block unreliable rankings

**Files:**
- Create: `strategy_shadow_report.py`
- Create: `tests/test_strategy_shadow_report.py`

- [ ] **Step 1: Write failing report-gate tests**

```python
@pytest.mark.parametrize("blocker", [
    "control_mirror_mismatch",
    "tick_gap",
    "telegram_lineage_incomplete",
    "money_contract_missing",
    "registered_after_outcome",
])
def test_report_refuses_to_rank_when_required_evidence_is_blocked(blocker):
    report = build_report(candidate_rows(blocker=blocker), actual_rows())
    assert report["ranking_allowed"] is False
    assert report["winner"] is None
    assert blocker in report["blockers"]
```

- [ ] **Step 2: Run report tests and verify RED**

Run: `python -m pytest tests/test_strategy_shadow_report.py -q`

Expected: collection fails because `strategy_shadow_report` does not exist.

- [ ] **Step 3: Implement signal, day and cohort summaries**

Join by channel and source signal identity. Report actual MT5 entry count, exit reason and net EUR; each candidate's entries, reason, net EUR, MFE and MAE; control price/time/money errors; blockers; daily/cumulative totals; and the nine cross-channel pairings calculated only by adding already-frozen individual results. Never mutate or refit candidate parameters.

- [ ] **Step 4: Add checkpoint and control-parity tests**

Require diagnostic/provisional/evidence labels at 15/45/100 untouched eligible signals. Require exact logic-mirror parity before a ranking, while causal-prediction slippage remains a measured field rather than an automatic mismatch.

- [ ] **Step 5: Run focused tests and commit**

Run: `python -m pytest tests/test_strategy_shadow_report.py -q`

Expected: all report tests pass.

Commit: `feat: report certified multichannel shadow comparisons`

### Task 7: Document operation and verify the complete change

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-27-multichannel-strategy-shadow-design.md` only if implementation details require an explicit clarified invariant

- [ ] **Step 1: Document the operator contract**

Document that shadows are disabled by default, never trade, zones are excluded, candidate parameters are frozen, normal differences do not alert the user, and a ranking is unavailable until its evidence gates pass. Include the exact rollback setting `STRATEGY_SHADOW_ENABLED=false`.

- [ ] **Step 2: Run focused shadow tests**

Run: `python -m pytest tests/test_strategy_shadow_catalog.py tests/test_strategy_shadow_engine.py tests/test_strategy_shadow_runtime.py tests/test_strategy_shadow_listener.py tests/test_strategy_shadow_report.py tests/test_public_safety.py -q`

Expected: all focused tests pass.

- [ ] **Step 3: Run compile and full repository verification**

Run: `python -m compileall -q strategy_shadow_contracts.py strategy_shadow_catalog.py strategy_shadow_engine.py strategy_shadow_runtime.py strategy_shadow_report.py listener.py main.py`

Expected: exit code 0 with no output.

Run: `python -m pytest -q`

Expected: the complete suite passes with no new failure.

- [ ] **Step 4: Inspect safety and repository state**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `rg -n "(^|\s)(import|from)\s+(executor|pending_actions|MetaTrader5)|order_send" strategy_shadow_*.py`

Expected: no matches.

Run: `git status --short --branch`

Expected: only the planned implementation and documentation changes are present; no runtime data is modified.

- [ ] **Step 5: Commit the verified implementation**

Commit: `feat: run frozen strategies in prospective shadow mode`

Do not push, deploy or restart the VM in this task. Deployment remains a separate explicit decision after review and requires no open MT5 positions.
