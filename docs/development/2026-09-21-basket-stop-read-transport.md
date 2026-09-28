# Basket Stop Read Transport

## Scope

Local component milestone, not admission of the complete Dubai or Gold555
strategy. No commit, publication, VM restart or live-order verification is
established by this evidence.

`basket_stop.py` extracts the existing basket loss-stop calculation and open
position lookup into shared sequential read generators. The executor drives
those same generators against its existing MT5 facade. The calculation retains
float arithmetic, leg order, repeated midpoint valuations and final rounding.

The offline read driver now accepts operation-specific finite profit scalars
and symbol metadata. Separate stop and position-plan cycles retain the existing
transport lifecycle: sampling is not delivery, a timeout does not free an active
native call, and an incomplete calculation cannot publish a trial stop.

The composed plan freezes selected positions at their read while later profit
valuations may observe a different conversion rate. It retains the separate
symbol reads made by the original call sequence. Unknown positions remain
distinct from a known empty set.

## Evidence

- Four executor characterization cases passed before and after extraction.
- The focused combined run passed 256 tests, including 24 new stop-read cases.
- Independent focused review reported no material findings; it is not a
  substitute for the full suite or historical validation.
- The full-suite evidence bundle is
  `reports/e5-basket-stop-read-transport-20260921/`. Its initial freeze contains
  392 source files. The full run passed 6,988 tests in 480.21 seconds, with
  zero failures, errors or skipped tests and 10 existing warnings (nine
  record_property/xunit2 warnings and one pandas chained-assignment warning).
  `verification.json` records exit code zero and unchanged source hashes and
  implementation identity. The JUnit totals and hash were independently checked:
  `7cc64fbf43ec44efc3a7da8d6f1bbcd02067607b5c71b3ff2ff0c12e80f038d8`.

The controls cover BUY/SELL, sequential changing valuations, contention with
another basket, explicit read limits, active-call timeout/cancellation, invalid
payloads, absent metadata, position snapshot stability and repeated corrections.
These are synthetic controls, not observed broker performance measurements.

## Remaining Boundary

The new stop-read flows are not yet connected to causal symbol/profit valuation
inside the suspended replay engine or to native protection installation. A
computed price is not an installed stop. Desired, queued, sent, accepted and
confirmed protection states must stay distinct.

Normal stop calculations need an explicit read budget larger than the ordinary
money-summary default of 128; the successful controls use 1024. The live
correction loop retains its prior behavior, while offline cycles are bounded by
their declared budgets/deadlines. Positive-price request validation remains in
force; small-price edge inputs are not silently widened into valid requests.

Next: integrate these reads with the actual replay state and protection
lifecycle, complete the monitor ordering for both channels, then compare full
historical windows including exposure, floating P/L and drawdown. Strategy
search admission and safe deployment remain separate subsequent gates.
