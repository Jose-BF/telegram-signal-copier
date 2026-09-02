# Gold Signals NOW Iterative Strategy Farm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse the proven causal research machinery to build a deterministic, independently verified strategy-discovery farm for every formal Gold Signals BUY/SELL NOW signal.

**Architecture:** Generalize the existing Dubai path and strategy contracts behind compatibility aliases, add a Gold-specific evidence adapter and grammar extensions, then run the same bounded diagnose-mutate-verify loop with chronological whole-day folds. A separate scalar oracle and immutable manifest block rankings whenever entry, ticks, money, actual MT5 evidence or provider timelines are incomplete.

**Tech Stack:** Python 3.11/3.14, NumPy, Numba, pandas, PyArrow, pytest, existing replay and broker-money contracts.

---

## File Map

- `research/dubai_iterative/dataset.py`: channel-neutral path loader plus Dubai wrapper.
- `research/dubai_iterative/contracts.py`: shared genome extensions with unchanged legacy fingerprints.
- `research/dubai_iterative/engine.py`: scalar causal engine extensions.
- `research/dubai_iterative/fast_engine.py`: matching accelerated extensions.
- `research/dubai_iterative/oracle.py`: independently implemented Gold semantics.
- `research/dubai_iterative/search.py`: caller-supplied folds and channel-neutral labels.
- `research/dubai_iterative/evolution.py`: diagnostics and mutations for the new blocks.
- `research/gold_iterative/dataset.py`: Gold NOW eligibility and provider-claim adapter.
- `research/gold_iterative/seeds.py`: diverse Gold generation-zero families.
- `research/gold_iterative/folds.py`: deterministic complete-day folds.
- `research/gold_iterative/reporting.py`: compact Gold artifacts and claim scorecard.
- `research/gold_iterative/__main__.py`: CLI, progress and resumable run.
- `tests/test_iterative_core_compatibility.py`: Dubai cent/fingerprint characterization.
- `tests/test_gold_iterative_*.py`: dataset, engine, oracle, search and reporting coverage.

### Task 1: Freeze Dubai compatibility before generalization

**Files:**
- Create: `tests/test_iterative_core_compatibility.py`

- [x] **Step 1: Record characterization assertions for representative Dubai
  genomes, engine results, oracle cents and current finalist fingerprints.**
- [x] **Step 2: Run and verify GREEN on the untouched implementation.**

Run: `python -m pytest tests/test_iterative_core_compatibility.py tests/test_dubai_iterative_contracts.py tests/test_dubai_iterative_engine.py tests/test_dubai_iterative_oracle.py -q`

- [x] **Step 3: Commit the characterization boundary.**

Run: `git add tests/test_iterative_core_compatibility.py && git commit -m "test: freeze iterative research compatibility"`

### Task 2: Generalize the certified dataset loader

**Files:**
- Modify: `research/dubai_iterative/dataset.py`
- Create: `research/gold_iterative/__init__.py`
- Create: `research/gold_iterative/dataset.py`
- Create: `tests/test_gold_iterative_dataset.py`

- [x] **Step 1: Write failing tests for `canal2`, `telegram_now` scope, exact
  audit gating, blocked-row retention and source-manifest hashes.**

```python
def test_gold_loader_accounts_for_every_now_signal(tmp_path):
    dataset = load_gold_now_dataset(**write_gold_fixture(tmp_path))
    assert dataset.eligible_signal_ids == ("canal2_10", "canal2_11")
    assert [path.signal_id for path in dataset.paths] == ["canal2_10"]
    assert dataset.exclusions["tick_replay_blocked"] == ("canal2_11",)
```

- [x] **Step 2: Run and verify RED.**

Run: `python -m pytest tests/test_gold_iterative_dataset.py -q`

- [x] **Step 3: Parameterize the existing loader by channel and source kind.**

Introduce channel-neutral `SignalLeg`, `SignalPath` and `StrategyDataset`, with
`DubaiLeg`, `DubaiPath` and `DubaiDataset` compatibility aliases. Keep
`load_dubai_dataset` as an unchanged wrapper. Gold's wrapper requires
`channel="canal2"` and `entry_source_kind="telegram_now"` and never silently
accepts zone plans.

- [x] **Step 4: Run Gold and all Dubai dataset tests.**

Run: `python -m pytest tests/test_gold_iterative_dataset.py tests/test_dubai_iterative_dataset.py tests/test_iterative_core_compatibility.py -q`

- [x] **Step 5: Commit.**

Run: `git add research tests && git commit -m "feat: load certified Gold NOW research paths"`

### Task 3: Extend the genome without changing old identities

**Files:**
- Modify: `research/dubai_iterative/contracts.py`
- Create: `tests/test_gold_iterative_contracts.py`

- [x] **Step 1: Write failing contract tests for adverse-reversal entry,
  per-leg target vectors, trailing protection, time-exit modes and
  explicit-close-only management.**

- [x] **Step 2: Add schema-v2 optional fields with neutral defaults.**

Add `entry_confirmation_value`, `target_steps`, `trailing_distance`,
`time_exit_mode` and `pending_entry_policy`. Schema-v1 canonical payloads omit
neutral schema-v2 fields so every existing Dubai fingerprint stays byte exact.

- [x] **Step 3: Encode frozen Gold 555 and c490 as genomes and assert their
  strategy fingerprints match the runtime registry.**

- [x] **Step 4: Run contract and compatibility suites.**

Run: `python -m pytest tests/test_gold_iterative_contracts.py tests/test_dubai_iterative_contracts.py tests/test_iterative_core_compatibility.py -q`

- [x] **Step 5: Commit.**

Run: `git add research/dubai_iterative/contracts.py tests && git commit -m "feat: extend iterative genome for Gold strategies"`

### Task 4: Implement Gold semantics in three independent engines

**Files:**
- Modify: `research/dubai_iterative/engine.py`
- Modify: `research/dubai_iterative/fast_engine.py`
- Modify: `research/dubai_iterative/oracle.py`
- Create: `tests/test_gold_iterative_engine.py`
- Create: `tests/test_gold_iterative_oracle.py`

- [x] **Step 1: Write scalar-engine tests covering BUY/SELL quote sides,
  adverse then reversal, temporary flat pending legs, per-fill targets,
  monotonic trailing SL, basket guard and same-tick stop priority.**
- [x] **Step 2: Run and verify RED.**
- [x] **Step 3: Implement the minimal scalar behaviour and verify GREEN.**
- [x] **Step 4: Write independent oracle tests before its implementation.**
- [x] **Step 5: Implement the oracle without importing scalar or fast engine
  transition helpers.**
- [x] **Step 6: Extend Numba fixed-point codes and require cent agreement on a
  parameterized matrix of directions and Gold families.**

Run: `python -m pytest tests/test_gold_iterative_engine.py tests/test_gold_iterative_oracle.py tests/test_dubai_iterative_engine.py tests/test_dubai_iterative_fast_engine.py tests/test_dubai_iterative_oracle.py -q`

- [x] **Step 7: Commit.**

Run: `git add research/dubai_iterative tests && git commit -m "feat: simulate Gold genomes with independent parity"`

### Task 5: Add diverse Gold seeds and bounded refinement

**Files:**
- Create: `research/gold_iterative/seeds.py`
- Modify: `research/dubai_iterative/evolution.py`
- Modify: `research/dubai_iterative/refinement.py`
- Create: `tests/test_gold_iterative_evolution.py`

- [x] **Step 1: Write failing tests requiring materially distinct generation
  zero families and finite anti-loop budgets.**
- [x] **Step 2: Implement seeds for provider baseline, immediate scale-out,
  adverse ladder, adverse-reversal, partial runner, basket capture, staged
  protection, short/long hold and no-entry controls.**
- [x] **Step 3: Add structured Gold diagnoses and single-cause mutations while
  retaining Dubai diagnosis behaviour.**
- [x] **Step 4: Collapse observationally equivalent outcomes and enforce
  novelty, lineage and no-improvement stops.**
- [x] **Step 5: Run evolution suites and commit.**

Run: `python -m pytest tests/test_gold_iterative_evolution.py tests/test_dubai_iterative_evolution.py tests/test_dubai_iterative_refinement.py -q`

Run: `git add research tests && git commit -m "feat: add bounded Gold strategy refinement"`

### Task 6: Chronological complete-day search

**Files:**
- Create: `research/gold_iterative/folds.py`
- Create: `research/gold_iterative/search.py`
- Create: `tests/test_gold_iterative_search.py`

- [x] **Step 1: Write failing tests that keep one trading day and overlapping
  baskets in one partition and forbid challenge data from mutation.**
- [x] **Step 2: Build expanding folds dynamically from complete available days,
  requiring at least two development and one later challenge day per fold.**
- [x] **Step 3: Wrap shared `run_chronological_search` with Gold seeds, manifest,
  deterministic seed and recorded envelope.**
- [x] **Step 4: Add the known train-positive/challenge-negative case as an
  overfitting rejection fixture.**
- [x] **Step 5: Run search tests and commit.**

Run: `python -m pytest tests/test_gold_iterative_search.py tests/test_dubai_iterative_search.py -q`

Run: `git add research/gold_iterative tests && git commit -m "feat: search Gold strategies chronologically"`

### Task 7: Provider-claim scorecard and honest reports

**Files:**
- Create: `research/gold_iterative/reporting.py`
- Create: `tests/test_gold_iterative_reporting.py`

- [x] **Step 1: Write failing report tests separating actual MT5 EUR, simulated
  EUR, provider pips claim and unverified accounting hypotheses.**
- [x] **Step 2: Implement immutable run cards, compact Parquet populations,
  frontier JSON, per-signal diagnostics, daily totals and claim distance.**
- [x] **Step 3: Block winner/ranking language when actual, ticks, money, oracle
  parity or chronological gates fail.**
- [x] **Step 4: Run reporting tests and commit.**

Run: `python -m pytest tests/test_gold_iterative_reporting.py -q`

Run: `git add research/gold_iterative tests && git commit -m "feat: publish honest Gold strategy evidence"`

### Task 8: CLI, deterministic acceptance and documentation

**Files:**
- Create: `research/gold_iterative/__main__.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Create: `tests/test_gold_iterative_cli.py`

- [x] **Step 1: Write failing CLI tests for inspect, search, resume, verify and
  compare-provider-claims commands.**
- [x] **Step 2: Implement progress with evaluated/total, fold, generation,
  elapsed time, ETA and explicit blockers; no whole population is printed.**
- [x] **Step 3: Run a bounded synthetic acceptance twice and require identical
  manifests, frontiers and cent results.**
- [x] **Step 4: Run the complete Dubai and Gold research suites, then the whole
  repository suite.**

Run: `python -m pytest tests/test_gold_iterative_*.py tests/test_dubai_iterative_*.py -q`

Run: `python -m pytest -q`

- [x] **Step 5: Document commands and confidence labels, then commit.**

Run: `git add research/gold_iterative README.md AGENTS.md tests && git commit -m "feat: complete Gold NOW iterative strategy farm"`

### Task 9: Real-corpus audit and first bounded run

- [ ] **Step 1: Update local telemetry without modifying the VM runtime.**
- [ ] **Step 2: Build the 2026-07-27-to-latest manifest and report exact,
  blocked, unfilled and missing counts before evaluating a strategy.**
- [ ] **Step 3: Run the independent baseline reproduction for legacy, 555 and
  c490; resolve every cent or lifecycle mismatch before search.**
- [ ] **Step 4: Run a bounded chronological exploration and oracle-check every
  finalist in all configured execution worlds.**
- [ ] **Step 5: Publish only evidence-supported conclusions and freeze any
  prospective demo candidate before its forward cohort begins.**
- [ ] **Step 6: Send the corpus manifest, blockers, run fingerprint and honest
  confidence status by Telegram.**
