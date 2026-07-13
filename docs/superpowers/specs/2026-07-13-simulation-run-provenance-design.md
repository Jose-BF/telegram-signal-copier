# Simulation Run Provenance Design

**Date:** 2026-07-13

**Goal:** Make every strategy-farm result attributable, repeatable and
comparable without changing the Telegram-to-MT5 production runtime.

## Why This Is Worth Building

`strategy_farm.py` already records the date scope, policy snapshots, metrics,
coverage and selection blockers. It does not currently prove which exact input
bytes, tick contracts or simulator source produced those metrics, and its
default output is overwritten by the next run.

The missing capability is therefore provenance, not another backtest engine.
This design adapts the useful run-card pattern seen in Vibe-Trading and the
run/input/artifact identity principles used by MLflow and DVC, while avoiding
their engines, services, dependencies and experiment-management workflows.

## Scope

Each provenance-complete strategy-farm execution will produce a
content-addressed run archive containing:

- an immutable machine-readable `run_card.json`;
- the compact `strategy_farm.json` result produced by that execution;
- deterministic hashes for the selected inputs, policies, simulator source
  and verified tick-day contracts;
- a compact summary of the result and its validation blockers.

The existing latest-result file, `data/strategy_farm.json`, remains available
with its current purpose and compatible top-level fields.

`data/provider_signal_catalog.json` becomes a versioned canonical pipeline
artifact. The watcher currently generates this file but deliberately omits it
from the data commit, which means the exact provider timeline used by a VM farm
run is unavailable after synchronizing another machine. Versioning one current
catalog fixes that evidence gap; the catalog is not copied into every run.

## Non-Goals

- Do not install or embed Vibe-Trading, MLflow or DVC.
- Do not replace the causal tick simulator or its selection gates.
- Do not add an experiment database, server, agent swarm or dashboard.
- Do not copy Parquet tick files, raw Telegram logs or detailed trade output
  into each run archive.
- Do not introduce durable large-artifact storage in this slice. A future DVC,
  object-storage or backup decision may preserve tick Parquet files outside
  Git, but the run card must not pretend that a hash can recreate a deleted
  file.
- Do not modify `main.py`, `listener.py`, `executor.py`, MT5 order handling or
  any live-trading decision.
- Do not claim that provenance creates a profitable strategy. It makes later
  profitability conclusions testable and auditable.

## Architecture

Create one focused module, `simulation_run_provenance.py`. It has no dependency
on MT5 and uses only the Python standard library. `strategy_farm.py` calls it
after the farm report has been built and before successful command completion.

`ReplayTickFrameCache` will expose the contract records it has already verified
while loading the farm's selected trades. This is observation only: it does not
change tick selection, simulation decisions or cache validation behavior.

The module receives paths and values that were actually used by the farm. It
does not rediscover alternative defaults or rerun any simulation. It builds a
canonical identity document, computes a SHA-256 fingerprint, validates any
existing archive with that fingerprint, and atomically publishes a new archive
when needed.

Default archive layout:

```text
data/simulation_runs/
  <64-character run fingerprint>/
    run_card.json
    strategy_farm.json
```

The archive is content-addressed, so rerunning identical inputs, code and
parameters does not create another directory.

## Run Identity

The deterministic run fingerprint is the SHA-256 of canonical JSON containing
only computational inputs:

- provenance schema version;
- farm parameters: date range, minimum sample, detail mode and horizon-related
  policy values already represented by the policy catalog;
- canonical policy catalog and its SHA-256;
- canonical selected-payload SHA-256 values for:
  - replay trades inside the requested date scope;
  - baseline audit rows used by those selected trades;
  - provider signals inside the requested date scope or linked to those
    selected executions;
- Python and result-relevant package versions (`pandas`, `numpy` and
  `pyarrow`);
- SHA-256 for the source files that define the result:
  - `strategy_farm.py`;
  - `strategy_policies.py`;
  - `strategy_simulator.py`;
  - `observed_tick_replay_validator.py`;
  - `tools/ensure_replay_tick_cache.py`;
  - `simulation_run_provenance.py`;
- every UTC tick day required by the selected replay trades, represented by
  its `.parquet.meta.json` contract, contract version and recorded Parquet
  SHA-256.

Absolute machine-specific paths, timestamps, output paths and Git branch names
are excluded from the fingerprint. Git commit, branch and dirty state are card
diagnostics; exact source bytes and runtime versions form the computational
identity. A dirty worktree is therefore identifiable without making unrelated
dirty files part of the fingerprint. Strict strategy selection remains
governed by the existing farm gates.

The provenance layer does not add a second hash pass over the raw Parquet
files. `ReplayTickFrameCache` already validates every loaded UTC-v2 sidecar
against its Parquet file; the farm passes those verified contract records to
the provenance module. Missing, invalid or unverified tick contracts appear as
provenance errors and prevent a valid run archive.

The card also records SHA-256, size and portable path for the complete
`replay_trades.jsonl`, `observed_tick_replay_audit.jsonl` and
`provider_signal_catalog.json` source artifacts, but those full-file hashes are
diagnostic and do not form the run fingerprint. The selected-payload hashes,
rather than unrelated rows elsewhere in a source file, form the run identity.
Therefore, adding data outside a closed
`--from`/`--to` window does not create a false new experiment. An open-ended
window legitimately changes identity when new in-scope signals arrive.

Canonical mappings use sorted keys, but computational list order is preserved.
In particular, replay-trade order affects drawdown and loss-streak metrics, and
policy order can affect deterministic tie resolution. The identity and result
hashes therefore retain selected trade order, policy order, rankings and any
chronological ticket/event sequences instead of sorting those lists away.

Tick identity and tick retention are separate facts. A verified sidecar proves
which Parquet bytes were used and detects any later mismatch. It does not make
those bytes durable. The card records whether every tick artifact is currently
available and marks retention as `local_cache_only` until an external durable
store is deliberately configured.

## Result Identity

The result receives a separate deterministic SHA-256 calculated from the farm
report after removing non-semantic generation timestamps and provenance output
paths. This distinguishes these cases:

- same run fingerprint and same result hash: idempotent rerun;
- different run fingerprint: a legitimate new experiment;
- same run fingerprint but different result hash: deterministic-replay
  violation, reported as an error instead of silently overwriting evidence.

The archived report is the compact farm output. Runs requested with
`--include-trades` still receive a run card, but their potentially large detail
report is not copied into the archive; the card records the external artifact
path, size and hash. This prevents accidental repository growth.

## Run Card Schema

`run_card.json` contains:

```json
{
  "schema_version": 1,
  "run_fingerprint": "sha256...",
  "result_fingerprint": "sha256...",
  "created_at_utc": "2026-07-13T00:00:00+00:00",
  "reproducibility": {
    "verified_now": true,
    "durable": false,
    "errors": [],
    "limitations": ["tick_artifacts_local_cache_only"],
    "git": {},
    "runtime": {},
    "parameters": {},
    "inputs": [],
    "source_files": [],
    "policy_catalog_sha256": "sha256...",
    "tick_days": []
  },
  "result_summary": {
    "provider_signals": 0,
    "executed_trades": 0,
    "policy_count": 0,
    "selected_policy": null,
    "selection_blockers": []
  },
  "artifacts": []
}
```

All JSON is strict: non-finite floating-point values become `null`, keys are
ordered for hashing, UTF-8 is used, and no credentials or environment values
are captured.

## Data Flow

1. `strategy_farm.py` loads the existing replay, baseline and provider files.
2. It executes the current causal farm without behavioral changes.
3. The provenance module receives the exact CLI configuration, selected trades,
   policy catalog, source paths, input paths and completed report.
4. It consumes the tick contracts already verified while those selected trades
   were loaded; it does not perform another Parquet hash pass.
5. It computes run and result fingerprints.
6. It validates an existing same-fingerprint archive or writes a new archive
   through temporary files followed by atomic replacement.
7. `strategy_farm.py` adds a small `provenance` reference to the latest report
   and keeps its existing console output and exit semantics on success.

## Failure Handling

- Missing required input: farm exits non-zero and no valid run card is
  published.
- Missing or invalid tick contract: existing simulation blockers remain
  visible in the latest report, provenance is marked incomplete, and no
  immutable archive is presented as reproducible. This condition does not turn
  an otherwise completed diagnostic farm run into a live-runtime failure.
- Existing archive with malformed card or mismatched artifact hash: fail
  closed; never overwrite it automatically.
- Same run fingerprint with a different result fingerprint: fail closed with a
  deterministic-replay violation.
- Interrupted write: temporary files may remain, but readers never see a
  partially published card or report.
- Failed provider-catalog or latest-farm regeneration: the mutable output is
  removed before the builder starts, so the watcher cannot stage an older file
  as if it belonged to the failed session. Existing content-addressed archives
  are never deleted by this cleanup.
- Git command unavailable: relevant source hashes still identify code, and the
  card records the Git diagnostic error.
- Deleted historical tick Parquet after publication: the card remains valid
  evidence of the original bytes but cannot claim that the run is currently
  repeatable; a newly downloaded file must match the recorded digest.

No provenance failure can alter or send an MT5 order. The watcher may report a
post-session farm failure, as it already does for analysis-pipeline failures,
without preventing the next live-bot start.

## Storage Policy

- Compact archives are deduplicated by fingerprint.
- No raw ticks, Telegram logs, credentials or detailed trade matrices are
  duplicated.
- One current canonical provider catalog is versioned as a shared pipeline
  input. The measured catalog is approximately 2.1 MB and is not duplicated per
  run; Git stores subsequent textual changes as deltas.
- The current compact farm report is approximately 84 KB, so one genuinely new
  daily run is expected to add less than roughly 100 KB.
- The archive directory is versioned because its purpose is cross-machine
  evidence; retention can later be based on date and validation value, but no
  deletion policy is introduced in this change.

## Compatibility

- Existing callers of `build_farm_report()` keep receiving the same report
  structure unless they explicitly invoke publication.
- Existing `strategy_farm.py` CLI arguments continue to work.
- The CLI gains an optional archive-directory override for tests and deliberate
  offline runs; the production default is `data/simulation_runs`.
- `data/strategy_farm.json` remains the latest report.
- The archive is generated only by the CLI publication path, keeping unit tests
  and library calls free of filesystem side effects.
- No production-runtime import points to the provenance module.
- The post-session watcher only stages newly published compact archive files;
  it does not execute provenance work inside the running bot process.
- The watcher also stages `data/provider_signal_catalog.json`, closing the
  current cross-machine evidence gap.
- Mutable catalog and latest-report files are cleared before regeneration and
  replaced only by successful builders; failed runs cannot reuse stale output.

## Verification

Tests must prove:

- canonical hashes are independent of dictionary key order, absolute path, Git
  branch and non-semantic timestamps;
- changing one input byte, policy value, source byte or tick digest changes the
  run fingerprint;
- changing only timestamps, output location or branch name does not change it;
- changing a result-relevant runtime version changes the run fingerprint;
- identical reruns are idempotent and do not duplicate archives;
- same-run/different-result conflicts fail closed;
- corrupt or incomplete tick contracts cannot produce a valid archive;
- tick retention is reported honestly as local-only rather than durable;
- the canonical provider catalog used by the farm is included in session-data
  commits;
- failed offline builders cannot leave a stale catalog or latest farm report
  eligible for the next data commit;
- detailed reports are referenced but not copied;
- secrets and environment values never appear in cards;
- the existing strategy-farm report and policy metrics remain unchanged;
- the complete project test suite still passes.

## Acceptance Criteria

The feature is valuable only if all of the following hold:

1. Given a farm result, its exact data, policy, source and tick identities can
   be established from one run card.
2. Repeating an identical run while the recorded artifacts are available
   returns the same run fingerprint and result fingerprint.
3. A contradictory result from identical evidence is rejected automatically.
4. No bot startup, message handling, MT5 execution or live latency path changes.
5. No new third-party dependency or background service is introduced.
6. Storage remains content-addressed and compact.
7. The card distinguishes verified identity from durable artifact retention;
   neither state is inferred from the other.

## References

- Vibe-Trading [`run_card.py`](https://github.com/HKUDS/Vibe-Trading/blob/main/agent/backtest/run_card.py):
  compact run evidence and artifact hashing under the MIT license.
- [MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking/): runs associate
  parameters, code versions, metrics, datasets and artifacts.
- [DVC Experiments](https://dvc.org/doc/start/experiments): experiments bind
  Git/code, parameters, metrics and data identity while keeping experiment
  history reproducible.
