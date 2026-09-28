# Observed, shadow and repair evidence

Read before changing parity gates or reporting live/shadow strategy comparisons.
These definitions clarify the intended evidence. They do not themselves change
the implementation, make a failed result pass, or certify existing shadow data.

## Three distinct checks

1. Observed accounting uses reconciled actual fills, quantities and costs.
   Reconcile each deal/position and the basket at account-currency precision.
   Match identity and entry count, not just an aggregate profit that may hide
   offsetting errors.
2. Policy decision replay uses the same strategy/version, causal inputs and
   decision ordering. Compare entries, levels, updates, partial closes, retries,
   transitions and final state as applicable, with traceable reasons.
3. Independent hypothetical execution uses simulated fills/costs. State the
   execution model, data gaps and predeclared sensitivity envelope. Do not
   promise cent equality to unknown alternative broker executions.

A valid control needs the applicable accounting and decision checks. A matching
net total alone is insufficient. For exact observed reconstruction, do not
widen tolerances to hide a money error. For hypothetical fills, do not change
the execution assumptions after observing results to manufacture agreement.

Record which checks a result actually passes. Missing evidence is unknown,
not success. Verification booleans and deltas must be recomputed from
authoritative evidence rather than trusted from runtime input.

The Gold live-management control uses report schema 4 and
`ledger_bound_entry_and_exit_deals_v4`. The authoritative observed facts come from
the broker ledger's positions and deals, not the simulated path's copies.
It binds position identity, observed entry facts, closed volume and money
per ticket as well as basket totals. Partial exits are summed within each
ticket, never across tickets to hide offsetting errors. The pipeline report
rechecks ticket and raw exit evidence against the ledger instead of trusting a
version token and success flags. Schema 1/2/3 certificates require
re-verification; they cannot establish this stronger claim. See
`../audits/2026-09-08-simulator-exit-sequence.md`.

Exit facts retain native position/deal/order identities, millisecond UTC,
entry/exit prices, volume, per-deal money and broker TP/SL mechanism. Same-time
partial deals compare as a multiset, not an invented within-millisecond order.
Expert/manual exits need a causal decision trace; close-by and allocation of
nonzero opening costs among simulated exits remain unsupported, not silently
approximated. The ledger API requires the caller to establish EUR using the
account snapshot; absence of currency in a ledger row is not independent
currency evidence. An explicitly contradictory currency is rejected.

The pure close-attribution diagnostic can bind a retained request, attempt and
confirmation to native broker deal/order identities. Retcodes alone are not
fills; redundant failed closes do not own a broker TP/SL. This diagnostic is
not a replacement for journal integrity or full policy replay, and is not
accepted as a shortcut through the pipeline's full-sequence gate.

The pipeline report exposes a separate exit-deal-facts gate but does not yet
carry an independently verified full exit decision/request/deal-sequence
contract. Its end-to-end extension gate remains closed
for that reason even if every entry count and monetary total happens to match.
This does not invalidate separately reconciled observed accounting, and must
not be mistaken for a universal ban on declared provider-first diagnostics.

## Repair incidents

A prospective discrepancy is an incident, not a discarded sample. Retain the
channel/signal, basket, strategy and source versions, exact evidence, check
category, root cause and action required.
Known unexplained control mismatches block the affected comparison/ranking
under its existing fail-closed gate. Do not relabel or broaden scope to evade it.

- Preserve a permanent regression for the original failing case and verify
  neighboring failure modes when the fix affects them.
- Keep local materialized registry and append-only
  strategy_shadow_incidents.jsonl telemetry consistent before publication.
- A later report omitting the basket does not close its incident.
- A covered defect recurring after resolution is a regression.
- Never use an unrelated passing basket to close a mismatch.

## Versions and prospective boundaries

A current-engine replay over an older registration is retrospective repair
evidence, not a new prospective shadow run. It cannot enter prospective totals,
rankings or promotion evidence merely because it now passes.

For a repair preserving the original strategy contract, rerun the same basket
and compare all applicable facts. If it still differs, record the repair-outcome
deltas and unresolved incident; a version blocker must not conceal the mismatch.

If strategy behavior intentionally changed, record both contracts and the
authorized difference before comparing. It is neither proof of repair nor an
automatic defect that a different policy produces a different result.
Check the historical policy with its own contract to resolve its incident.
Any policy or engine change still requires the relevant regression and
deployment review; an instruction edit cannot authorize it.

## Recursive reliability

Logs may propose patterns, never modify live rules or promote themselves.
recursive_log_learning.py rebuilds the pattern registry from the retained
corpus so repeated runs do not double-count evidence.
A pattern becomes covered only with a versioned rule, permanent regression,
successful whole-corpus shadow evaluation under that contract and review
metadata. Run that evaluation when claiming coverage, not for every log query.

Capture, semantics, execution, accounting, market replay, provenance and
simulation gates remain distinct. A failed hard gate keeps the affected report
diagnostic_only and prevents certified policy selection.
