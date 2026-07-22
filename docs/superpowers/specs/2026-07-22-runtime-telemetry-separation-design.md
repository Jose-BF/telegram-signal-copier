# Runtime Telemetry Separation Design

## Objective

Production logging must never change the Git checkout that runs the bot. A
Telegram, MT5, disk, network or telemetry-publication failure must not block
startup, restart, code updates or signal processing.

Commit `016059a` made recovery lossless and moved heavy analysis out of the
restart path, but it still committed raw evidence to `main`. This design
removes that remaining coupling.

## Storage Boundary

- `main` owns code, tests, configuration templates and documentation.
- The existing tracked `data/` files remain a read-only migration seed. The
  runtime never appends to them after this change.
- Live evidence is stored under `BOT_RUNTIME_DATA_DIR`, defaulting to the
  ignored `runtime_data/` directory beside the code checkout.
- Console diagnostics are written to `runtime_data/bot_runtime.log` so
  Telegram/MT5 transport errors remain available without entering `main`.
- On first launch, the four authoritative streams are copied atomically from
  the legacy seed when their runtime equivalents do not exist:
  `trade_events.jsonl`, `trade_journal.csv`, `trade_events_TEST.jsonl` and
  `trade_journal_TEST.csv`.
- A migration manifest records source size and SHA-256 so the copy is
  auditable. Existing runtime files always win; startup never overwrites them.

JSONL and CSV remain the canonical formats. They are already append-only,
human-inspectable and consumed by replay. Changing database format here would
add migration risk without improving the evidence contract.

## Independent Transport

An independent telemetry command reads only complete appended records and
creates immutable gzip chunks. Every chunk has a sidecar manifest containing:

- source stream;
- byte start and end offsets;
- uncompressed size and SHA-256;
- creation time and active code commit.

The cursor advances atomically only after the chunk and manifest are durable.
Chunk names derive from stream, offsets and content hash, so retrying after a
crash is idempotent.

Checkpoint and publication each use an exclusive, stale-safe lease. A
successful remote push removes only the confirmed local outbox copies; a
failed push leaves every file in place. Re-exporting the same byte range from
a new code version accepts the existing immutable range/hash while retaining
the metadata from its first publication.

Publication uses a separate local checkout and an orphan `telemetry` branch.
It never checks out, commits, rebases or pushes `main`. Network errors leave
the outbox intact and return a diagnostic status; they never affect the bot.
The watcher launches publication as a non-blocking side process and performs
only a local, bounded checkpoint during shutdown or restart.

## Analysis Contract

`runtime_paths.py` provides the one path policy used by production and replay:

- when `BOT_RUNTIME_DATA_DIR` is set, inputs and outputs use that directory;
- otherwise tools prefer an initialized local runtime store and fall back to
  the historical `data/` seed for an unchanged development checkout.

A materialization command can fetch the telemetry branch, verify every chunk,
reject gaps or overlaps and rebuild the exact streams. Analysis never reads a
file while the production process is writing it. On a clean analysis checkout,
the default destination is ignored `runtime_data/`, never tracked `data/`, and
a manifest marks the verified corpus as active.

## Watcher Behaviour

Startup order becomes:

1. Preserve/migrate any legacy raw evidence into the runtime store.
2. Repair legacy generated files without committing data.
3. Verify and update code from `origin/main`.
4. Start the bot immediately.
5. Trigger telemetry publication in the background.

The watcher no longer creates `data:` commits, publishes local data commits or
treats telemetry transport as a condition for running. Local source-code edits
remain a hard block because executing unknown code is unsafe.

## Failure Handling

- Partial JSONL tail: archive the partial bytes and export only through the
  last newline.
- Interrupted export: deterministic chunk is recreated on the next run.
- GitHub unavailable: retain outbox and retry later; bot remains active.
- Telemetry branch conflict: fetch/retry inside the isolated checkout; never
  touch the production checkout.
- Existing dirty legacy data: migrate it before restoring the tracked seed.
- Existing dirty source code: stop and report the exact paths.

## Acceptance Criteria

- Running the bot changes no tracked file in the production checkout.
- Starting and stopping the bot creates no commit on `main`.
- A failed telemetry push cannot delay or terminate the bot.
- A legacy VM upgrade preserves every complete raw event and journal row.
- Re-materialized streams are byte-identical to the exported complete prefix.
- All focused tests and the complete test suite pass.
