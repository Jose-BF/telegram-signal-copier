# VM Analysis And Push Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep full VM-generated analytical evidence while publishing a compact latest report, a lossless verified archive and live pipeline progress.

**Architecture:** Add one reusable console progress reporter. Extend simulation provenance with backward-compatible gzip artifacts and expose a compact latest farm report that points to the immutable full archive. Thread progress callbacks through the strategy loops and show stage progress from the production watcher without changing the canonical raw-data staging list.

**Tech Stack:** Python 3.11+, pytest, gzip, JSON, existing Git watcher and simulation provenance modules.

---

### Task 1: Console Progress Reporter

**Files:**
- Create: `pipeline_progress.py`
- Create: `tests/test_pipeline_progress.py`

- [ ] **Step 1: Write failing rendering and throttling tests**

Test a deterministic `render_progress(current, total, label, width=10)` string,
zero-total handling and a reporter that emits completion even when throttled.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m pytest tests/test_pipeline_progress.py -q`

Expected: import failure because `pipeline_progress.py` does not exist.

- [ ] **Step 3: Implement the minimal reporter**

Provide `render_progress`, `ProgressReporter.update` and
`ProgressReporter.complete`. Use fixed-width ASCII bars, monotonic elapsed time,
carriage-return updates on interactive streams and newline updates otherwise.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_pipeline_progress.py -q`

Expected: all progress tests pass.

### Task 2: Lossless Compressed Run Archives

**Files:**
- Modify: `simulation_run_provenance.py`
- Modify: `tests/test_simulation_run_provenance.py`

- [ ] **Step 1: Write failing gzip round-trip and tamper tests**

Require new non-detailed runs to retain `strategy_farm.json.gz`, record
`compression: gzip`, stored hash and uncompressed hash, reload the canonical
JSON during idempotence checks, reject corrupt gzip bytes and continue reading
legacy `strategy_farm.json` archives.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_simulation_run_provenance.py -q`

Expected: new gzip assertions fail against the current JSON artifact.

- [ ] **Step 3: Implement backward-compatible artifact readers and writers**

Add deterministic gzip encoding (`mtime=0`), artifact-byte decoding based on
the run-card compression field and validation of both stored and canonical
hashes. Do not change run or result fingerprint semantics.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_simulation_run_provenance.py -q`

Expected: legacy and gzip provenance tests pass.

### Task 3: Compact Latest Strategy Report

**Files:**
- Modify: `strategy_farm.py`
- Modify: `tests/test_strategy_farm.py`

- [ ] **Step 1: Write a failing compact-report test**

Require the latest output to retain scope, validation, policy scores, selection,
money checks and provenance while replacing `provider_policy_results` with an
`archive` reference containing the complete artifact path and fingerprints.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_strategy_farm.py -q`

Expected: latest output still contains full provider rows.

- [ ] **Step 3: Implement compact projection after archive publication**

Keep the full report for evidence and archive publication. Write only the
compact projection to `--output` when `--include-trades` is false. Preserve the
existing exact-output contract when `--include-trades` is true.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_strategy_farm.py -q`

Expected: compact and detailed CLI modes pass.

### Task 4: Strategy Work-Unit Progress

**Files:**
- Modify: `strategy_farm.py`
- Modify: `tests/test_strategy_farm.py`

- [ ] **Step 1: Write failing callback accounting tests**

Require monotonically increasing progress across executed-trade policy rows and
provider-signal policy/latency rows, ending exactly at the advertised total.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_strategy_farm.py -q`

- [ ] **Step 3: Add an optional progress callback and `--progress` CLI flag**

Progress remains opt-in and does not affect report identity. The watcher uses
`--quiet --progress` so calculations are visible without verbose summaries.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_strategy_farm.py -q`

### Task 5: Watcher Stage And Publication Progress

**Files:**
- Modify: `tools/run_bot_watch.py`
- Modify: `tools/git_sync.py`
- Modify: `tests/test_run_bot_watch.py`
- Modify: `tests/test_git_sync.py`

- [ ] **Step 1: Write failing stage-order and Git-progress tests**

Require the watcher to report every causal builder in existing order, show
strategy-farm progress, report staged byte size, and expose fetch/push/final
verification callbacks from Git synchronization.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_run_bot_watch.py tests/test_git_sync.py -q`

- [ ] **Step 3: Implement progress without changing safety gates**

Wrap builder calls with stage start/completion timing, stream strategy progress,
calculate staged size after `git add`, and pass a callback into the existing Git
state machine. Keep all existing rebase, rescue, clean-tree and remote-head
verification behavior.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_run_bot_watch.py tests/test_git_sync.py -q`

### Task 6: Full Verification And Size Contract

**Files:**
- Modify only files required by failures found in verification.

- [ ] **Step 1: Run the focused pipeline suite**

Run: `python -m pytest tests/test_pipeline_progress.py tests/test_simulation_run_provenance.py tests/test_strategy_farm.py tests/test_run_bot_watch.py tests/test_git_sync.py -q`

- [ ] **Step 2: Generate one temporary farm and verify losslessness**

Run the CLI against test fixtures, decompress the archive, verify its result
fingerprint against `run_card.json`, and confirm the compact latest output has no
`provider_policy_results` array.

- [ ] **Step 3: Run the complete suite**

Run: `python -m pytest -q`

Expected: all tests pass with the existing intentional skip count unchanged.

- [ ] **Step 4: Inspect Git scope**

Run: `git diff --check` and `git status -sb`.

Expected: only the planned code, tests and documentation are modified; no
runtime data is regenerated in the development worktree.
