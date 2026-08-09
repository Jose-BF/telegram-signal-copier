# Live Strategy And Shadow Validation Design

## Objective

Deploy one conservative demo-account strategy change for the next fresh
session while preserving enough causal evidence to evaluate it exactly after
the fact. The change must improve the treatment of large loss tails without
claiming that the small calibration sample proves profitability.

## Evidence Boundary

The robust sample is 2026-07-27 through 2026-08-07. It contains 103 signals
and reconciles to the observed MT5 result of -351.34 EUR. All existing days
are calibration data. The first session after this deployment is forward
evidence and must remain untouched by later parameter selection.

Increasing volume is outside scope. The current maximum planned exposure of
0.05 lots per signal remains unchanged.

## Dubai Investing

Dubai Investing keeps its existing entries, provider TP/SL, provider
management messages, and scale-out behavior. A basket guard is added on top:

- Close the signal basket when live floating P/L is at or below -50 EUR.
- Arm a profit lock after live floating P/L first reaches +30 EUR.
- Once armed, close the basket if live floating P/L returns to +20 EUR or
  lower.
- Apply only to `canal1` and only while MT5 still reports open positions.
- Trigger once per signal. Queue every close through the existing durable
  pending-action system and record the decision, thresholds, observed P/L,
  ticket set, and result path in the journal.

The guard uses account-currency P/L read from MT5. It does not infer money
from price movement and does not scale thresholds with lot size silently.

## Gold Signals

Immediate `BUY/SELL NOW` signals keep their current behavior. New zone plans
are handled as follows:

- A first market touch is observation-only. It is journaled once per zone
  generation, including side, price, tick time, and zone.
- A complete current generation is opened only after the provider sends an
  explicit `Active` instruction.
- Explicit re-entry remains supported when the provider asks for it and the
  plan has not forbidden re-entry.
- Incomplete, expired, invalidated, ambiguous, or stale generations cannot
  open positions.
- Explicit activation keeps the existing five-leg scale-out and therefore
  the existing 0.05-lot maximum. This isolates trigger timing as the only
  strategy change.

The historical zone sample remains negative under every tested entry policy.
`Active`-only is therefore a forward experiment, not a profitability claim.

## Canonical Evidence

Provider price bundles must not stay anchored to an obvious stale hundreds
prefix when stronger market or corrected-provider evidence exists. Canonical
reconstruction will accept a prefix repair only when one coherent shift fixes
the complete range/TP/SL bundle and the selected bundle is uniquely closest
to independently observed execution context. Provider corrections remain in
the timeline and never get overwritten by older generations.

Counterfactual money conversion must receive the verified broker UTC offset
from the exact market-tick contract. Missing or inconsistent clock evidence
blocks the money result instead of silently assuming UTC.

## Safety And Rollback

Both live changes have environment switches. The defaults requested for the
demo deployment are:

- Dubai basket guard enabled with -50/+30/+20 EUR thresholds.
- Gold first-touch execution disabled.
- Gold explicit activation enabled.

Startup logs and the journal publish the active strategy contract. Setting
the guard switch off and Gold first-touch switch on restores the previous
entry behavior without a code rollback.

## Verification

Required checks before push:

1. Unit tests prove every guard transition, channel isolation, idempotency,
   first-touch observation, and explicit activation path.
2. Regression tests reproduce the stale-prefix and UTC-offset defects.
3. Focused suites pass.
4. The complete test suite passes from a fresh invocation.
5. Only intended files are committed; the four pre-existing local replay
   edits remain uncommitted and excluded.
6. The remote branch is verified before and after pushing to `main`.

