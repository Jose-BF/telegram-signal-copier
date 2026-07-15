# Daily Replay Acceptance Contract

**Date:** 2026-07-15
**Status:** approved for implementation

## Goal

Make each retained trading day independently auditable before it enters
strategy experiments. The live trading path must remain unchanged.

## Root Cause

The observed-tick validator currently treats the nearest Bid/Ask quote and the
MT5 deal price as if they were the same fact. They are not: the quote proves a
market path, while the deal records the broker execution, including slippage.
This conflation rejected every July baseline even though MT5 accounting and the
tick-clock contracts were available.

The readiness report also checks only that a Parquet file exists. It does not
verify the UTC-v3 sidecar, its hash, or its semantic anchor evidence. Finally,
one invalid historical day can make the whole cache dependency fail even when
the selected July simulation window is complete.

## Evidence Dimensions

The implementation keeps four independent facts:

1. **Accounting exactness:** MT5 deal components reproduce account-currency
   P&L with a `0.00` difference. A reconstructed journal may remain a warning
   when MT5 deals still provide exact money evidence.
2. **Tick contract validity:** every selected day has a matching Parquet file
   and UTC-v3 sidecar with byte hash and semantic anchor validation.
3. **Causal path verification:** ticks support the recorded entry/exit time,
   active SL/TP timeline, first-touch reason and close timing.
4. **Execution delta:** MT5 fill minus contemporaneous quote is recorded as
   observed slippage. It is not silently converted into a market-path failure.

`exact` in the observed replay remains the compatibility status for a verified
causal path. The artifact must additionally identify the contract as
`causal_path_v2` and state that MT5 deals are the fill-price authority.

## External Closures

MT5 close reason `other` is an external/manual market close. It is replayed at
its observed timestamp, requires nearby verified ticks, retains the actual MT5
deal price for accounting, and records the quote-to-fill delta. It does not
pretend that an SL or TP was touched.

This covers the known Canal 1 SELL opened on 2026-07-09 and manually closed
after the VM power interruption.

## Selected Window

All offline builders use one simulation scope. The default starts at
`2026-07-06`, with compatibility for the existing
`STRATEGY_FARM_FROM_DATE` environment variable. Historical data remains on
disk and in Git; it is not deleted or relabelled. It simply cannot contaminate
the acceptance state of a later selected window.

Status artifacts must publish the scope explicitly, including selected trade
count and required tick days.

## Daily Acceptance Report

`replay_readiness_report.py` becomes the single human-facing daily contract.
For every selected trade it reports:

- core and ticket evidence;
- exact MT5 money evidence or named accounting degradation;
- strict UTC-v3 tick-contract evidence;
- observed causal-path status;
- warnings, including execution slippage;
- one of `ready`, `pending`, or `blocked`.

It also publishes a cohort summary per signal date. Open operations are
`pending`, not failed; they are reevaluated automatically after closure.
Closed operations with missing or contradictory evidence are `blocked`.

## Watcher Flow

The post-session order is:

1. ledger and replay reconstruction;
2. exact accounting audit;
3. selected-window tick assurance;
4. selected-window observed causal replay;
5. daily acceptance report;
6. provider catalog and strategy farm;
7. recursive learning publication.

Every stage remains best effort and offline. Failure never changes, delays, or
blocks a live MT5 order.

## Safety Gates

- No live entry, lot, SL, TP, BE, close, or Gemini behavior changes.
- Missing and invalid data fail closed with named blockers.
- Price deltas are measured, never discarded.
- Counterfactual fills remain model assumptions; only observed MT5 fills are
  exact account history.
- Strategy ranking remains disabled while money, OOS, semantic, or provenance
  gates are open.

## Acceptance Criteria

- A valid UTC-v3 contract is required, not just a Parquet filename.
- Normal observed execution deltas do not invalidate a causal market path.
- A shifted or missing tick timeline still blocks replay.
- Manual/external closes replay at their observed timestamp.
- `--since` scopes tick, observed-replay and readiness artifacts consistently.
- The watcher invokes the builders in causal order and stages all outputs.
- Targeted tests and the full repository suite pass before integration.

