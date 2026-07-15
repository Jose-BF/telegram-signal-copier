# Recursive Reliability Closure Design

**Date:** 2026-07-15

**Goal:** Complete the existing offline learning loop so every claimed bot fix
is backed by reproducible evidence, stale reports cannot look current, and a
future recurrence is reported as a regression.

## Scope

This is the first closure layer of the replay and simulation project. It does
not change entries, volume, exits, Telegram interpretation or MT5 order
execution. It makes the existing reliability learning system operational and
auditable before exact replay and strategy optimization continue.

The implementation will:

1. create a tracked review ledger for pattern decisions;
2. provide one safe command for covering or dismissing a pattern;
3. verify coverage evidence instead of trusting manually entered booleans;
4. publish an explicit freshness and build-status artifact after every VM
   session upload;
5. detect a covered pattern that appears again after its coverage time;
6. backfill only fixes whose commit and executable regression test can be
   proven from the repository.

Historical research scripts, dormant DCA code and replay accuracy corrections
remain separate work. They must not be mixed into this change.

## Safety Boundary

The learning path remains offline and one-way:

```text
retained evidence -> deterministic detector -> candidate pattern
                  -> verified human review -> covered/dismissed state
                  -> later evidence -> still covered or regressed
```

Runtime trading modules must not import the learner, review tool or generated
learning artifacts. No frequency threshold, Gemini output, report or review
record may edit source code, submit an MT5 action or alter live strategy.

## Components

### 1. Deterministic Pattern Detector

`recursive_log_learning.py` remains responsible for reading the retained
Telegram, execution, accounting, replay, provider and strategy artifacts. It
normalizes observations into stable pattern IDs and writes:

- `data/log_learning_report.json` for current health and priorities;
- `data/log_pattern_registry.json` for the derived pattern lifecycle.

The detector consumes review records but never creates or changes them. Given
identical source artifacts and review records, its report and registry remain
byte-identical.

### 2. Review Ledger

`data/log_pattern_reviews.json` is the only tracked source of manual lifecycle
decisions. It uses a versioned object keyed by stable `pattern_id` values.

A covered record contains `status`, `rule_version`, the full 40-character
`fix_commit`, an exact `regression_test` pytest node, reviewer identity, and
tool-assigned UTC review and coverage timestamps. Its `verification` object
records the tested `HEAD`, source SHA-256, and successful results for the exact
test, complete suite and deterministic corpus rebuild. For example, the real
invalid-stop regression node is
`tests/test_pending_actions.py::TestModifyPreconditions::test_invalid_stop_waits_without_mt5_submission`.

A dismissed record requires a reviewer, timestamp and non-empty reason. A
dismissal is a documented triage decision, not proof that code covers the
pattern. Derived occurrence counts, dates and evidence never live in this
ledger; rebuilding logs cannot overwrite human review.

### 3. Verified Review Command

`tools/review_log_pattern.py` is the sole supported writer for the ledger. It
supports `cover` and `dismiss` operations.

Before `cover` changes the ledger, it must:

1. rebuild the current registry and confirm the exact pattern exists;
2. resolve `fix_commit` and confirm it is an ancestor of the current `HEAD`;
3. ask `pytest` to collect the exact regression-test node;
4. execute that exact test and require exit code zero;
5. execute the complete repository test suite and require exit code zero;
6. rebuild the whole retained learning corpus twice in isolated temporary
   outputs and require byte-identical report and registry files;
7. record the current commit and source fingerprint from those verified
   outputs;
8. write the ledger atomically only after every check succeeds.

The command sets review and coverage timestamps itself. A caller cannot submit
`shadow_corpus_passed: true` or any equivalent shortcut. If any command,
fingerprint or validation fails, the existing ledger remains byte-identical.

Before `dismiss` changes the ledger, it confirms that the pattern exists,
requires a reason and reviewer, rebuilds the current corpus once, and records
its source fingerprint. Dismissing does not run the full suite because it does
not claim executable coverage.

### 4. Publication Status

`data/log_learning_status.json` is the authoritative freshness contract for
the two derived learning artifacts. It contains:

- schema version and UTC attempt time;
- success or failure;
- the local git commit and dirty state;
- named dependency results from accounting, observed ticks, provider catalog
  and strategy farm generation;
- source and review-ledger fingerprints;
- report and registry SHA-256 values;
- latest retained evidence timestamp;
- a publication ID derived from all fingerprints;
- explicit blockers and `conclusions_allowed`.

The report and registry are current only when the status says `ok`, their
hashes match and their publication ID corresponds to the current source
fingerprint. A prior report may remain available for diagnosis after a failed
attempt, but it is unambiguously stale and cannot authorize simulation or
strategy conclusions.

## Watcher Data Flow

At the end of a VM session, `tools/run_bot_watch.py` will keep rebuilding the
existing artifacts in dependency order. Recursive learning then runs once
regardless of whether an upstream builder passed:

1. collect the outcome of every upstream builder;
2. attempt the deterministic learning build with all available inputs;
3. atomically write the publication status in both success and failure paths;
4. stage the status together with the report, registry and review ledger when
   present;
5. print a concise summary naming failed dependencies.

An upstream failure therefore produces a fresh negative status instead of
silently leaving July 13 results beside July 15 logs. Learning failure remains
best-effort for bot availability: it cannot prevent the watcher from restarting
the live bot, but it blocks offline conclusions.

## Lifecycle Rules

- `observed`: evidence exists but does not meet candidate priority rules.
- `candidate`: recurring or material evidence requires engineering review.
- `covered`: a verified ledger record exists and no evidence occurs after the
  tool-generated coverage timestamp.
- `regressed`: covered evidence occurs strictly after that timestamp.
- `dismissed`: a reviewer documented why no code change is warranted.

If review evidence references a missing commit, nonexistent test, changed test
node or incompatible schema, the detector fails closed for that review. It
must not display the pattern as covered.

## Error Handling

- JSON and schema errors identify the exact file and field.
- Review writes use a temporary file plus atomic replacement.
- Subprocess output is captured and summarized without discarding the full
  command or exit code from the verification result.
- A failed coverage attempt never partially changes the ledger.
- A status write is attempted even if the learner raises an exception.
- Missing optional evidence becomes a named health blocker; missing required
  raw evidence cannot be interpreted as zero incidents.
- Review-ledger conflicts require an explicit new review command; they are not
  merged implicitly.

## Testing Strategy

Implementation follows red-green-refactor TDD. Tests will prove:

1. coverage rejects unknown patterns, missing commits and nonexistent pytest
   nodes;
2. the referenced pytest node is actually executed, not merely stored;
3. a failed test, full suite or corpus rebuild leaves the ledger untouched;
4. successful verification writes normalized evidence atomically;
5. dismissal requires auditable human reasoning;
6. report and registry rebuilds remain byte-deterministic;
7. pre-coverage observations remain covered while later observations become
   regressions;
8. a successful watcher build publishes matching hashes and fingerprints;
9. each upstream or learner failure publishes a current negative status;
10. the watcher runs the learning publisher even when observed-tick or provider
    generation fails;
11. default tracked artifacts exactly match a clean rebuild;
12. the full repository suite passes before publication.

## Initial Backfill

The first ledger will not mark patterns covered from memory or from comments.
For each proposed historical fix, the review command must find the actual
introducing commit and an existing executable regression-test node. Records
that cannot meet this bar remain candidates. This may initially leave some
known fixes uncovered, which is more honest than manufacturing proof.

## Acceptance Criteria

1. A developer cannot mark a pattern covered by editing a boolean through the
   supported workflow.
2. Every covered record names a reachable commit and a pytest node that passed
   during promotion.
3. Failed promotion leaves the review ledger unchanged.
4. Every watcher session publishes a status for the latest build attempt,
   including failed dependency names.
5. Stale report or registry bytes cannot pass the freshness contract.
6. A post-coverage recurrence is visible as `regressed` on the next build.
7. No live trading behavior or strategy parameter changes.
8. All new targeted tests and the complete repository suite pass.

## Non-Goals

- Automatic source-code modification or automatic promotion.
- Optimizing lot size, entries, break-even, partial exits or targets.
- Correcting millisecond replay alignment in this change.
- Capturing Telegram image bytes in this change.
- Completing or deleting dormant DCA and historical research modules.
- Claiming profitability or exact simulation from learning status alone.
