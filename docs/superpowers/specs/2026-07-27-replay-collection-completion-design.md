# Replay Collection Completion

**Date:** 2026-07-27

## Goal

Finish the executed-MT5 replay foundation before the official research
window starts. Every selected ticket must either close under an explicit
policy or produce a visible blocker. A calendar boundary must never decide a
trade implicitly.

## Scope

This phase completes three boundaries:

1. repository verification must not depend on ignored artifacts from one
   developer worktree;
2. tester runs must declare an exclusive end date;
3. positions still open at the end date remain blocked and cannot contribute
   to a policy total.

It does not select or deploy a profitable live strategy.

## Tester Horizon

`mt5_tester_replay.py prepare` accepts `--tester-until YYYY-MM-DD`. The value
is the exclusive tester end date and must be later than the selected signal
day.

- Omitted: use the following calendar day, preserving the current daily proof.
- Supplied: continue replaying real ticks across days.
- A TP2 policy keeps its virtual position until TP2 or provider SL.
- A ticket still open at the declared horizon is blocked as `horizon_open`.
- The run card records the exact start and exclusive end dates.
- Every generated INI uses the same recorded end date.

This makes “carry until TP2/SL” explicit. A future fixed-time or end-of-day
exit is a different strategy and must receive a separate policy identifier;
it must not be inferred from the tester boundary.

## Repository Verification

Tests that validate the tracked provider catalog use the tracked `data/`
corpus, not whichever ignored runtime store happens to be active. Temporary
strategy-farm tests create a complete sibling input bundle, including the
broker money contract. When a custom replay file is selected and no explicit
money contract is supplied, the farm resolves the sibling
`broker_money_contract.json`.

The tick-cache portability test derives its expected repository-relative path
from the active data boundary instead of hard-coding the legacy directory.

## Acceptance

The phase passes only when:

1. the new horizon tests fail before implementation and pass afterward;
2. the MQL5 EA compiles with zero errors and warnings;
3. the 2026-07-22 baseline still certifies 29 tickets and -57.60 EUR;
4. the previously open 2026-07-21 no-BE tickets either resolve under an
   explicit extended horizon or remain visibly blocked;
5. the full Python suite passes without requiring copied runtime artifacts;
6. the worktree is clean and no production push or VM restart occurs without
   user confirmation.
