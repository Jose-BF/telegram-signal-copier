# Provider-Centric Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce one causal simulation row per formal Telegram provider signal and policy, including signals that the live bot did not execute.

**Architecture:** Extend the canonical provider catalog with an explicit entry contract, transform each formal signal into a pure virtual-trade specification, and replay that specification over verified bid/ask ticks. Keep observed MT5 tickets as a separate validation layer and publish no monetary ranking until the broker monetary contract is implemented.

**Tech Stack:** Python 3.14, dataclasses, pandas/NumPy, pytest, existing UTC-v3 tick loader and JSON run-card provenance.

---

## File Map

- Modify `provider_signal_catalog.py`: publish deterministic causal entry contracts.
- Create `provider_trade_spec.py`: validate and transform canonical signals into virtual trade specifications.
- Create `provider_strategy_simulator.py`: select causal entry ticks and replay current policies without MT5 tickets.
- Modify `strategy_farm.py`: iterate canonical provider signals and retain an explicit row for every signal/policy pair.
- Modify `simulation_run_provenance.py`: fingerprint provider-first payloads and latency scenarios.
- Modify `tools/run_bot_watch.py`: stage the provider-first report only after successful offline generation.
- Modify `tests/test_provider_signal_catalog.py`: entry-contract regressions.
- Create `tests/test_provider_trade_spec.py`: pure specification tests.
- Create `tests/test_provider_strategy_simulator.py`: bid/ask, causality and policy tests.
- Modify `tests/test_strategy_farm.py`: complete-scope and no-silent-drop tests.
- Modify `tests/test_simulation_run_provenance.py`: fingerprint coverage.
- Modify `AGENTS.md` and `README.md`: record the provider-first pipeline and diagnostic money boundary.

## Task 1: Canonical Entry Contract

**Files:**
- Modify: `provider_signal_catalog.py`
- Test: `tests/test_provider_signal_catalog.py`

- [ ] **Step 1: Write failing tests for Canal 1 and Canal 2 entry contracts**

Add assertions equivalent to:

```python
entry = signal["entry_contract"]
assert entry == {
    "status": "ready",
    "trigger_observed_utc": "2026-07-08T11:44:02.181+00:00",
    "trigger_telegram_utc": "2026-07-08T11:44:01+00:00",
    "trigger_message_id": 20765,
    "trigger_kind": "sticker",
    "direction": "SELL",
    "direction_source": "telegram_understood",
    "blockers": [],
}
```

Cover a Canal 2 root whose direction appears in the first text revision and a
Canal 1 sticker/text pair whose text arrives after the sticker.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```powershell
python -m pytest tests/test_provider_signal_catalog.py -q
```

Expected: FAIL because `entry_contract` is absent.

- [ ] **Step 3: Track direction provenance while building the catalog**

Add private fields to `_empty_signal`:

```python
"_direction_source": None,
"_direction_observed_utc": None,
```

When `_append_revision` parses a direction, set source to
`revision_parser:<message_id>` and its observed timestamp. When
`telegram_understood` supplies sticker direction, set source to
`telegram_understood` without overwriting a parser source for the same trigger.

- [ ] **Step 4: Build the entry contract in `_finalize`**

Select revisions by `(observed_ts_utc, message_id)`. A revision is actionable
when it contains a parsed direction, or when it is the root sticker and the
final signal direction came from `telegram_understood`. Emit:

```python
{
    "status": "ready" if not blockers else "blocked",
    "trigger_observed_utc": observed,
    "trigger_telegram_utc": telegram,
    "trigger_message_id": message_id,
    "trigger_kind": "sticker" if sticker_id is not None else "text",
    "direction": signal.get("direction"),
    "direction_source": signal.get("_direction_source"),
    "blockers": blockers,
}
```

Use named blockers `missing_direction` and `missing_actionable_entry_trigger`.
Remove all private fields before returning the public catalog.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_provider_signal_catalog.py -q
git add provider_signal_catalog.py tests/test_provider_signal_catalog.py
git commit -m "feat: publish canonical provider entry contracts"
```

Expected: provider catalog tests pass.

## Task 2: Pure Virtual Trade Specifications

**Files:**
- Create: `provider_trade_spec.py`
- Create: `tests/test_provider_trade_spec.py`

- [ ] **Step 1: Write failing tests for executed and unexecuted signals**

Tests must assert that both signals create a specification and preserve
execution links only as evidence:

```python
spec = build_trade_spec(signal, latency_ms=250, volume_per_leg=0.01)
assert spec.provider_signal_id == "canal2_3200"
assert spec.execution_sig_ids == ()
assert spec.entry_ready is True
assert spec.latency_ms == 250
assert spec.leg_count == 6
```

Also cover a direction-only sticker: it creates one leg and reports
`missing_provider_tps` under `policy_evidence_gaps`, but remains entry-ready.

- [ ] **Step 2: Run tests and verify module-not-found failure**

```powershell
python -m pytest tests/test_provider_trade_spec.py -q
```

- [ ] **Step 3: Implement immutable specification types**

Create:

```python
@dataclass(frozen=True)
class ProviderTradeSpec:
    provider_signal_id: str
    channel: str
    direction: str | None
    trigger_observed_utc: datetime | None
    latency_ms: int
    volume_per_leg: float
    leg_count: int
    provider_tps: tuple[float, ...]
    provider_sl: float | None
    level_timeline: tuple[dict, ...]
    management_events: tuple[dict, ...]
    execution_sig_ids: tuple[str, ...]
    entry_blockers: tuple[str, ...]
    policy_evidence_gaps: tuple[str, ...]

    @property
    def entry_ready(self) -> bool:
        return not self.entry_blockers
```

`build_trade_spec` accepts only `record_type == "formal_signal"`, parses the
entry timestamp as aware UTC, validates non-negative latency and positive
volume, keeps every TP in provider order and uses one leg when no TP exists.

- [ ] **Step 4: Run tests and commit**

```powershell
python -m pytest tests/test_provider_trade_spec.py -q
git add provider_trade_spec.py tests/test_provider_trade_spec.py
git commit -m "feat: build provider virtual trade specifications"
```

## Task 3: Causal Virtual Entry Selection

**Files:**
- Create: `provider_strategy_simulator.py`
- Create: `tests/test_provider_strategy_simulator.py`

- [ ] **Step 1: Write failing Ask/Bid and latency tests**

Use a three-row UTC tick frame. Assert:

```python
buy = select_entry_tick(buy_spec, ticks)
assert buy.price == 4100.25
assert buy.side == "ask"

sell = select_entry_tick(sell_spec, ticks)
assert sell.price == 4100.00
assert sell.side == "bid"
```

Set `latency_ms=250` and prove the selector chooses the first tick at or after
`trigger_observed_utc + 250ms`, never the prior tick. Cover missing ticks and
missing ticks after trigger with named blockers.

- [ ] **Step 2: Run tests and verify failure**

```powershell
python -m pytest tests/test_provider_strategy_simulator.py -q
```

- [ ] **Step 3: Implement entry result and selector**

Create:

```python
@dataclass(frozen=True)
class VirtualEntry:
    status: str
    time_utc: datetime | None
    price: float | None
    side: str | None
    latency_ms: int
    blockers: tuple[str, ...]
```

Normalize timestamps to aware UTC, sort only when needed and use
`numpy.searchsorted` over nanosecond timestamps. Reject non-positive Bid/Ask.

- [ ] **Step 4: Run tests and commit**

```powershell
python -m pytest tests/test_provider_strategy_simulator.py -q
git add provider_strategy_simulator.py tests/test_provider_strategy_simulator.py
git commit -m "feat: select causal provider entry ticks"
```

## Task 4: Provider Policy Price-Path Replay

**Files:**
- Modify: `provider_strategy_simulator.py`
- Modify: `tests/test_provider_strategy_simulator.py`

- [ ] **Step 1: Write failing tests for policy replay**

Cover:

- provider TP/SL become active only at `observed_ts_utc`;
- a pre-level price touch does not close a leg;
- `no_be` ignores a later BE request;
- `close_2_be_1_runner_2` allocates nearest TPs first;
- no management trigger leaves all legs on provider SL/TP;
- a missing provider TP blocks only policies requiring a TP;
- one output row is returned even when blocked.

Expected result shape:

```python
{
    "provider_signal_id": "canal2_3200",
    "status": "simulated_price_path",
    "result_unit": "xauusd_price_units",
    "money_status": "unverified",
    "strategy_value": 12.5,
    "blockers": [],
    "legs": [...],
}
```

- [ ] **Step 2: Run the focused tests and verify failure**

```powershell
python -m pytest tests/test_provider_strategy_simulator.py -q
```

- [ ] **Step 3: Synthesize virtual legs and reuse causal level helpers**

Build virtual ticket dictionaries with stable labels
`virtual:<provider_signal_id>:<index>`, a common entry time/price and the
configured volume. Reuse `_provider_level_events`, `_management_trigger`,
`_ticket_actions` and `_first_strategy_close` from `strategy_simulator.py`.

When no management trigger exists, call `_first_strategy_close` directly with
provider SL/TP events. Do not use `_unchanged_ticket_result`, because there is
no observed MT5 close for a virtual trade.

- [ ] **Step 4: Mark all monetary output honestly**

Price-path replay may calculate directional XAUUSD price units, but set:

```python
"result_unit": "xauusd_price_units",
"money_status": "unverified",
"strategy_pnl": None,
```

Never place this result in a monetary ranking.

- [ ] **Step 5: Run tests and commit**

```powershell
python -m pytest tests/test_provider_strategy_simulator.py -q
git add provider_strategy_simulator.py tests/test_provider_strategy_simulator.py
git commit -m "sim: replay policies from canonical provider signals"
```

## Task 5: Provider-First Farm Coverage

**Files:**
- Modify: `strategy_farm.py`
- Modify: `tests/test_strategy_farm.py`

- [ ] **Step 1: Write failing no-silent-drop tests**

Build a catalog with three formal signals: one executed, one unexecuted and one
with missing entry evidence. For two policies assert six rows exist:

```python
assert report["provider_scope"]["formal_signals"] == 3
assert report["provider_scope"]["rows_expected"] == 6
assert report["provider_scope"]["rows_emitted"] == 6
assert report["provider_scope"]["signals_omitted"] == []
```

The missing-entry signal must have a blocked row for each policy.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
python -m pytest tests/test_strategy_farm.py -q
```

- [ ] **Step 3: Add provider-first execution as a separate report section**

Keep existing executed-ticket validation intact under
`executed_baseline_validation`. Add `provider_policy_results`, grouped by
policy, using every in-range formal catalog signal and configured latency
scenarios.

Report:

```python
"validation": {
    "price_path_mode": "provider_first",
    "money_mode": "diagnostic_only",
    "market_replay_verified": existing_value,
},
"selection": {
    "selected_policy": None,
    "global_blockers": ["broker_money_contract_unverified"],
    "exploratory_ranking": [],
}
```

Any row-count mismatch raises `RuntimeError` before publication.

- [ ] **Step 4: Run farm tests and commit**

```powershell
python -m pytest tests/test_strategy_farm.py -q
git add strategy_farm.py tests/test_strategy_farm.py
git commit -m "sim: cover every canonical provider signal in farm"
```

## Task 6: Provenance And Offline Publication

**Files:**
- Modify: `simulation_run_provenance.py`
- Modify: `tools/run_bot_watch.py`
- Modify: `tests/test_simulation_run_provenance.py`
- Modify: `tests/test_run_bot_watch.py`

- [ ] **Step 1: Write failing fingerprint tests**

Assert that changing any of these changes the run fingerprint:

- one provider signal revision;
- entry latency scenario order or value;
- volume per virtual leg;
- provider policy result bytes;
- verified tick contract.

- [ ] **Step 2: Include provider-first inputs in computational identity**

Add selected canonical signal payloads, ordered latency scenarios and virtual
entry configuration to `simulation_run_provenance.py`. Publication time,
machine path and Git branch remain diagnostic only.

- [ ] **Step 3: Keep watcher publication fail-closed**

The watcher stages the report and immutable run directory only when the farm
returns success and row accounting is complete. A diagnostic-only result is a
valid build, but stale prior outputs are deleted before the attempt.

- [ ] **Step 4: Run tests and commit**

```powershell
python -m pytest tests/test_simulation_run_provenance.py tests/test_run_bot_watch.py -q
git add simulation_run_provenance.py tools/run_bot_watch.py tests/test_simulation_run_provenance.py tests/test_run_bot_watch.py
git commit -m "sim: fingerprint provider-first farm inputs"
```

## Task 7: Documentation And Corpus Shadow Verification

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-07-14-provider-centric-simulation.md`

- [ ] **Step 1: Update project navigation and safety boundaries**

Document `provider_trade_spec.py`, `provider_strategy_simulator.py`, the
provider-first farm section, latency scenarios and the rule that price-unit
results cannot be presented as money.

- [ ] **Step 2: Run focused suites**

```powershell
python -m pytest tests/test_provider_signal_catalog.py tests/test_provider_trade_spec.py tests/test_provider_strategy_simulator.py tests/test_strategy_farm.py tests/test_simulation_run_provenance.py tests/test_run_bot_watch.py -q
```

Expected: all focused tests pass.

- [ ] **Step 3: Run the complete suite**

```powershell
python -m pytest -q
```

Expected: all tests pass; the existing intentional skip remains documented.

- [ ] **Step 4: Run a retained-corpus shadow build without overwriting canonical data**

Use temporary output and run-archive paths outside `data/`. Verify:

```text
rows_expected == rows_emitted
signals_omitted == []
selected_policy == null
broker_money_contract_unverified is present
```

Do not interpret price-unit outputs as profitability.

- [ ] **Step 5: Mark plan checkboxes, commit locally and stop before push**

```powershell
git add AGENTS.md README.md docs/superpowers/plans/2026-07-14-provider-centric-simulation.md
git commit -m "docs: record provider-first simulation workflow"
git status -sb
```

Expected: clean local worktree ahead of `origin/main`. Do not push while the
production bot is active.

## Follow-Up Plan Boundary

After this plan passes, write a separate broker monetary contract plan covering
symbol snapshots, conversion-symbol discovery, multi-symbol UTC-v3 tick
retention, deal-level cent reconciliation, commission and rollover evidence.
Only after that plan passes may the farm publish monetary rankings or expand
beyond the current smoke-test policy catalog.
