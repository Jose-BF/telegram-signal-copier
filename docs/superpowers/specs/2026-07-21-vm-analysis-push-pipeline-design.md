# VM Analysis And Push Pipeline Design

## Objective

Keep all evidence required to reconstruct Telegram signals and MT5 execution,
while making the VM analysis and Git publication pipeline visible, smaller and
less disruptive to the live bot.

The VM remains responsible for deterministic reconciliation, replay validation,
provider reconstruction, strategy simulation and recursive log learning. This
change does not move analytical work to an AI and does not remove detail.

## Non-Negotiable Evidence

The following production data remains canonical and is always published:

- `data/trade_events.jsonl`: append-only Telegram, bot and MT5 event stream.
- `data/trade_journal.csv`: runtime trade summary and observed excursions.
- `data/ledger.jsonl`: MT5-reconciled positions, deals and money results.
- Replay, accounting and observed-tick audits and their status documents.
- Provider signal catalog and recursive-learning reports.
- The complete result of every new strategy-farm fingerprint.

No raw event is sampled, summarized away or discarded by this change.

## Output Layout

Each strategy-farm run has two representations:

1. A compact latest report at `data/strategy_farm.json`. It contains scope,
   validation, policy metrics, rankings, blockers, provenance and a pointer to
   the complete archive, but not the repeated per-signal policy rows.
2. One immutable complete archive in `data/simulation_runs/<fingerprint>/`.
   New archives store the full canonical JSON as `strategy_farm.json.gz`.
   `run_card.json` records the compression format, stored-file hash and the
   existing canonical result fingerprint. Existing uncompressed archives stay
   valid and readable.

The archive is lossless. Decompressing it yields the same report structure the
simulator produced before compression. The compact report is the normal input
for quick log analysis; the full archive is opened only for trade-level audit.

## Progress Contract

The production console shows one coherent pipeline instead of appearing idle:

- Global stages use `[current/total]`, a fixed-width bar, stage name and elapsed
  time.
- Strategy simulation reports completed work units over the total for executed
  and provider-centric policies.
- Artifact publication reports compact and archive sizes.
- Git synchronization reports preparation, commit, fetch, push and final
  verification. Native Git transfer output remains available on failures.
- Progress output is rate-limited and flushed so PowerShell displays it live.

`--quiet` continues to suppress ordinary summaries. A separate progress flag
allows the watcher to show progress while keeping command output concise.

## Failure And Freshness Rules

- Files are written through temporary paths and atomically promoted.
- A complete archive is published only after its hashes and run card validate.
- A failed current run never modifies an immutable previous archive.
- The compact latest report is removed or marked unavailable when the current
  inputs cannot produce a valid result; previous archives remain accessible.
- The watcher commits only after the pipeline finishes its consistency checks.
- Git success still requires verified `main == origin/main`, a clean worktree
  and no rebase in progress.

## Compatibility

- Readers accept both legacy `strategy_farm.json` archives and new
  `strategy_farm.json.gz` archives.
- Existing run fingerprints and result fingerprints remain semantic hashes of
  the uncompressed canonical data, so compression cannot change a result's
  identity.
- Tests cover archive round trips, tamper detection, compact-report contents,
  progress rendering, pipeline order and the exact staged evidence list.

## Success Criteria

- Full farm details remain recoverable and hash-verifiable.
- The latest report used for routine analysis is small and excludes repeated
  trade-level rows.
- The console updates throughout long analysis and publication phases.
- A no-op or failed rebuild does not create a large add/delete cycle.
- The complete test suite passes before publication.
