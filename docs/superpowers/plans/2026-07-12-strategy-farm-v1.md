# Strategy Farm v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an auditable multichannel strategy farm that replays canonical Telegram signals over MT5 bid/ask ticks and compares dozens of causal management policies without selecting a false winner.

**Architecture:** Raw Telegram perception and MT5 facts remain immutable inputs. A canonical provider-signal layer groups both channels into the same event model; a policy catalog describes how many legs to close, protect at breakeven, or keep as runners; the simulator executes those policies over one shared tick timeline. Baseline validation and data-quality gates are mandatory before any result is eligible for strategy selection.

**Tech Stack:** Python 3.14, pytest, pandas, numpy, parquet, JSONL, MetaTrader5 Python API.

---

### Task 1: Make Tick Time UTC-Exact and Auditable

**Files:**
- Modify: `tools/ensure_replay_tick_cache.py`
- Modify: `mt5_tick_cache.py`
- Modify: `observed_tick_replay_validator.py`
- Test: `tests/test_ensure_replay_tick_cache.py`
- Test: `tests/test_observed_tick_replay_validator.py`

- [x] **Step 1: Write a failing UTC contract test**

Create a fake MT5 module whose current tick is stale but whose `copy_ticks_range` expects aware UTC datetimes. Assert that `MT5TickSource.fetch_ticks()` sends the original UTC interval unchanged and creates `time_utc` directly from `time_msc` without applying an inferred offset.

- [x] **Step 2: Run the targeted test and confirm RED**

Run: `python -m pytest tests/test_ensure_replay_tick_cache.py -q`

Expected: FAIL because the current source shifts the request and returned timestamps by `offset_h`.

- [x] **Step 3: Implement the official MT5 UTC contract**

Remove live-tick offset inference from `MT5TickSource`. Require timezone-aware UTC inputs, call `copy_ticks_range(symbol, t_from_utc, t_to_utc, COPY_TICKS_ALL)`, and derive `time_utc` directly from `time_msc`.

- [x] **Step 4: Add cache provenance and alignment diagnostics**

Store a cache contract version and `time_basis=mt5_utc` in the status output. Extend observed replay rows with nearest open/close tick deltas so a shifted cache is diagnosed as a time-alignment failure rather than a plausible opposite TP/SL.

- [x] **Step 5: Run targeted tests and confirm GREEN**

Run: `python -m pytest tests/test_ensure_replay_tick_cache.py tests/test_observed_tick_replay_validator.py -q`

Expected: PASS.

### Task 2: Build the Canonical Provider-Signal Catalog

**Files:**
- Create: `provider_signal_catalog.py`
- Test: `tests/test_provider_signal_catalog.py`

- [x] **Step 1: Write failing grouping tests**

Cover Canal 2 progressive edits (`BUY/SELL NOW`, range, targets, SL), reply-based management, Canal 1 sticker plus later text, text-only recovery, duplicate poll/update events, and a provider signal that the bot never executed.

- [x] **Step 2: Run the targeted test and confirm RED**

Run: `python -m pytest tests/test_provider_signal_catalog.py -q`

Expected: FAIL because the catalog does not exist.

- [x] **Step 3: Implement immutable canonical records**

Create one record per provider signal with `provider_signal_id`, channel, direction, risk label, raw revisions, entry-zone timeline, TP/SL timeline, management events, Telegram timestamps, parser provenance, and execution links. Preserve incomplete records and list explicit gaps; never discard them silently.

- [x] **Step 4: Add coverage accounting**

Report raw provider signals, canonical signals, linked bot executions, missed executions, duplicate executions, unresolved replies, and incomplete semantic records by channel and day.

- [x] **Step 5: Run targeted tests and confirm GREEN**

Run: `python -m pytest tests/test_provider_signal_catalog.py -q`

Expected: PASS.

### Task 3: Define Declarative Management Policies

**Files:**
- Create: `strategy_policies.py`
- Test: `tests/test_strategy_policies.py`

- [x] **Step 1: Write failing policy-catalog tests**

Assert that a five-leg signal generates unique causal policies for every valid allocation of `close_now`, `move_to_be`, and `runner`, that allocations always sum to the available leg count, and that the same definitions clamp safely to four-leg Canal 1 signals.

- [x] **Step 2: Run the targeted test and confirm RED**

Run: `python -m pytest tests/test_strategy_policies.py -q`

Expected: FAIL because the policy model does not exist.

- [x] **Step 3: Implement the policy model**

Define an immutable `StrategyPolicy` with entry policy, leg allocation, BE trigger, partial-close trigger, TP assignment, original SL behavior, time horizon, weekend behavior, lot/risk model, and human-readable assumptions. Generate the initial close/protect/runner matrix plus `follow_actual` and `no_be` controls.

- [x] **Step 4: Run targeted tests and confirm GREEN**

Run: `python -m pytest tests/test_strategy_policies.py -q`

Expected: PASS.

### Task 4: Generalize the Event-Driven Simulator

**Files:**
- Modify: `strategy_simulator.py`
- Test: `tests/test_strategy_simulator.py`

- [x] **Step 1: Write failing causal replay tests**

Cover first tick after a management message, close-nearest-target legs first, BE on protected legs, untouched original SL on runners, TP before management, SL before management, BUY bid and SELL ask execution, no future knowledge, and exact no-op behavior.

- [x] **Step 2: Run the targeted test and confirm RED**

Run: `python -m pytest tests/test_strategy_simulator.py -q`

Expected: FAIL because only `no_be` is currently supported.

- [x] **Step 3: Implement one shared chronological engine**

Replace the name-specific branch with policy-driven per-leg state. Sort tickets by causal TP distance, merge level changes and provider management events into the tick timeline, choose the earliest valid close event, and emit an audit trail for every changed decision.

- [x] **Step 4: Preserve compatibility**

Keep the existing `no_be` CLI and report schema usable while adding policy IDs and policy snapshots. A trade without a relevant management event must retain MT5 actual P/L exactly.

- [x] **Step 5: Run targeted tests and confirm GREEN**

Run: `python -m pytest tests/test_strategy_simulator.py -q`

Expected: PASS.

### Task 5: Run and Score the Strategy Farm

**Files:**
- Create: `strategy_farm.py`
- Test: `tests/test_strategy_farm.py`

- [x] **Step 1: Write failing metrics and selection-gate tests**

Cover net P/L, expectancy, win rate, profit factor, maximum drawdown, worst trade, maximum loss streak, coverage, assumptions, channel breakdown, and rejection of rankings when any baseline is invalid or the sample is below the configured minimum.

- [x] **Step 2: Run the targeted test and confirm RED**

Run: `python -m pytest tests/test_strategy_farm.py -q`

Expected: FAIL because the farm does not exist.

- [x] **Step 3: Implement batch execution and honest ranking**

Run every policy against the identical selected signal set and tick cache. Produce an exploratory ranking for debugging, but expose `selected_strategy=null` until all strict gates pass. Separate results by channel and preserve every blocked signal in each policy report.

- [x] **Step 4: Add robustness metrics**

Calculate MFE capture, profit giveback, spread/slippage sensitivity scenarios, and per-day/per-channel stability. Do not annualize ratios over the short validation window.

- [x] **Step 5: Run targeted tests and confirm GREEN**

Run: `python -m pytest tests/test_strategy_farm.py -q`

Expected: PASS.

### Task 6: End-to-End Evidence Gate

**Files:**
- Modify: `README.md`
- Modify: `tools/run_bot_watch.py`
- Test: `tests/test_run_bot_watch.py`

- [x] **Step 1: Add a daily pipeline command**

Regenerate ledger, replay trades, accounting audit, tick-cache status, observed replay, canonical provider catalog, and strategy-farm diagnostics in a fixed order. Strategy-farm failures must not stop the live bot, but must remain visible and must not publish a winner.

- [x] **Step 2: Verify the complete suite**

Run: `python -m pytest -q --disable-warnings`

Expected: all tests pass with one known optional Gemini skip.

- [x] **Step 3: Run the clean-window audit**

Run the full pipeline for `2026-07-06` onward. Confirm the report states exactly how many provider signals from each channel were reconstructed, linked, missed, exact, mismatched, blocked, and eligible for exploratory simulation.

- [x] **Step 4: Inspect repository state**

Run: `git diff --check` and `git status -sb`.

Expected: no whitespace errors and only intentional source, test, documentation, and generated-report changes.

### Task 7: Causal Review Hardening

- [x] Require entry and exit bid/ask alignment, first-touch reason, time and fill price before baseline status can be exact.
- [x] Reject future TP information, execution-derived BE triggers and management events that precede any ticket fill.
- [x] Feed counterfactual policies from canonical Telegram levels and management timestamps observed by the bot.
- [x] Preserve A -> B -> A edits, single-price entries and dated orphan management replies.
- [x] Verify every tick-cache day with a UTC-v2 SHA-256 manifest and reject legacy/tampered parquet files at load time.
- [x] Exclude blocked or estimated policies from exploratory ranking and reject stale watcher artifacts after failed builders.
- [x] Re-run the full suite, regenerate the clean-window report and inspect final repository state.
