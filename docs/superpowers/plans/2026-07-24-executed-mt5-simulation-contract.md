# Executed MT5 Simulation Contract Implementation Plan

**Goal:** Make executed MT5 trades the primary, fail-closed strategy universe
without removing provider-first diagnostics or changing live trading.

## Tasks

- [x] Add failing tests for MT5 level-history authority and immutable entries.
- [x] Add failing farm tests for complete trade/policy row accounting.
- [x] Implement a pure executed-replay contract validator.
- [x] Make the farm use confirmed MT5 levels while retaining canonical Telegram
      management-trigger times.
- [x] Publish executed-MT5 validation and selection as the primary report.
- [x] Keep provider-first results explicitly diagnostic and fingerprinted.
- [x] Update provenance validation for the new primary mode.
- [x] Bind replay artifacts to the exact ledger and event hashes that built them.
- [x] Update current documentation and watcher-facing compact summaries.
- [x] Run focused tests, then the full test suite.
- [x] Run a deterministic retained-data smoke test without touching production.

## Acceptance Evidence

Clean window `2026-07-06` through `2026-07-24`:

- 132 executed MT5 trades and 616 tickets checked;
- 131 exact causal trade replays plus one labelled external intervention;
- zero replay mismatches and zero blocked trades;
- 616/616 observed ticket results reconciled to the cent;
- 22 policies produced all 2,904 required rows;
- zero blocked policy rows and zero MT5 entry-invariant failures;
- a replay rebuilt from the current ledger/events was byte-identical to the
  independently validated replay;
- repeated farms produced the same result fingerprint;
- strategy selection remained correctly blocked only by
  `oos_not_validated`.

The full repository suite passed with 1,394 tests.
