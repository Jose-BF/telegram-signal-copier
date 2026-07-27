# Replay Collection Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the MT5 replay window explicit, resolve cross-day policy runs, and restore hermetic repository verification.

**Architecture:** Extend the existing deterministic tester profile generator with one exclusive horizon date recorded in the run card. Keep all position semantics inside the existing tester-only EA; the horizon only controls available real ticks and never fabricates a close. Repair test fixtures so custom replay bundles contain every mandatory input.

**Tech Stack:** Python 3.14, pytest, MQL5 build 6061, MetaTrader 5 Strategy Tester Model 4.

---

### Task 1: Hermetic repository tests

**Files:**
- Modify: `tests/test_ensure_replay_tick_cache.py`
- Modify: `tests/test_provider_signal_catalog.py`
- Modify: `tests/test_strategy_farm.py`
- Modify: `strategy_farm.py`

- [ ] **Step 1: Preserve the current failing-suite evidence**

Run:

```powershell
python -m pytest tests/test_ensure_replay_tick_cache.py::test_cache_status_uses_repo_relative_cache_dir_for_default_cache tests/test_provider_signal_catalog.py::test_versioned_catalog_exactly_matches_default_corpus_rebuild tests/test_strategy_farm.py::test_cli_writes_latest_report_with_run_card_reference -q
```

Expected: all three fail because of a hard-coded legacy path, an ignored
runtime catalog, and a missing custom money contract.

- [ ] **Step 2: Make the fixtures independent of ignored runtime files**

Use the tracked legacy corpus for the versioned-catalog assertion. Create a
valid `broker_money_contract.json` beside every temporary custom replay.
Change the CLI money-contract default to `None`, then resolve it from the
selected replay directory exactly as ledger and events are resolved.

- [ ] **Step 3: Verify focused tests**

Run:

```powershell
python -m pytest tests/test_ensure_replay_tick_cache.py tests/test_provider_signal_catalog.py tests/test_strategy_farm.py -q
```

Expected: all pass.

- [ ] **Step 4: Commit**

```powershell
git add strategy_farm.py tests/test_ensure_replay_tick_cache.py tests/test_provider_signal_catalog.py tests/test_strategy_farm.py
git commit -m "test: isolate replay verification inputs"
```

### Task 2: Explicit cross-day tester horizon

**Files:**
- Modify: `mt5_tester_replay.py`
- Modify: `tests/test_mt5_tester_replay.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing horizon tests**

Require `prepare_run(..., tester_until=date(2026, 7, 31))` to write
`ToDate=2026.07.31` to all profiles and this exact window to the run card.
Require same-day or earlier values to raise
`FixtureBlockedError("invalid_tester_until")`.

- [ ] **Step 2: Run tests and observe the missing argument failure**

Run:

```powershell
python -m pytest tests/test_mt5_tester_replay.py -q
```

Expected: failure because `tester_until` and `--tester-until` are absent.

- [ ] **Step 3: Implement the minimal horizon contract**

Thread `tester_until` through `_ini_text`, `prepare_run`, CLI parsing and the
run card. Default to `day + timedelta(days=1)` and validate it is later than
the selected day.

- [ ] **Step 4: Verify and document**

Run:

```powershell
python -m pytest tests/test_mt5_tester_replay.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add mt5_tester_replay.py tests/test_mt5_tester_replay.py README.md
git commit -m "feat: declare cross-day MT5 replay horizons"
```

### Task 3: Cross-day MT5 proof

**Files:**
- Generated only: `runtime_data/mt5_tester_runs/2026-07-21/**`

- [ ] **Step 1: Prepare the 2026-07-21 fixture with an extended horizon**

Run:

```powershell
python mt5_tester_replay.py prepare --date 2026-07-21 --tester-until 2026-07-24 --expect-signals 14 --expect-tickets 67 --expect-pnl-eur -38.28
```

- [ ] **Step 2: Run baseline and both policies from fresh local terminals**

Require 67 tickets for every result. The baseline must remain -38.28 EUR.
No policy total may be shown if any ticket remains open or blocked.

- [ ] **Step 3: Recheck the 2026-07-22 regression proof**

Require 29 tickets and -57.60 EUR with the current compiled EA.

### Task 4: Final verification and integration readiness

**Files:**
- No new production files.

- [ ] **Step 1: Compile the tester EA**

Expected: 0 errors, 0 warnings.

- [ ] **Step 2: Run focused tests**

```powershell
python -m pytest tests/test_mt5_tester_replay.py tests/test_mt5_replay_ea_contract.py tests/test_broker_money.py tests/test_accounting_replay_validator.py -q
```

- [ ] **Step 3: Run the complete suite**

```powershell
python -m pytest -q
```

Expected: zero failures.

- [ ] **Step 4: Inspect Git**

```powershell
git diff --check
git status -sb
git log --oneline origin/main..HEAD
```

- [ ] **Step 5: Stop before production**

Do not update `origin/main`, the VM, or the running bot until the user confirms
there is no open operation and explicitly approves deployment.
