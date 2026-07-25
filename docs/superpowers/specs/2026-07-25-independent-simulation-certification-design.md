# Independent Simulation Certification

## Purpose

The strategy farm must never present a policy ranking from a result that is
only internally plausible. Every counterfactual result must be independently
recomputed and proved ticket by ticket.

This contract separates two different questions:

1. Did the simulator reproduce the declared rules exactly?
2. Does the strategy generalize and make money out of sample?

Certification answers only the first question. Statistical validation remains
a separate mandatory gate.

## Safety rule

No aggregate P&L, ranking, or selected policy is conclusion-eligible unless
all eligible tickets have an independent certificate.

The system fails closed. Missing, conflicting, ambiguous, stale, or mutated
evidence produces a blocker. It never chooses a convenient interpretation.

## Two engines

### Candidate engine

The existing `strategy_simulator.py` and `broker_money.py` remain the engine
under test.

### Independent oracle

`simulation_oracle.py` recomputes the same declared policy without importing
either candidate module. It independently implements:

- MT5-entry preservation;
- policy allocation and leg ordering;
- confirmed level history;
- first-touch detection using Bid for BUY exits and Ask for SELL exits;
- management closes;
- stop gap fills at the first executable quote;
- TP fills at the configured target under the currently observed broker
  contract;
- end-of-day horizon;
- account-currency conversion and cent rounding.

The engines may share immutable source artifacts. They must not share price
path, policy, or money-calculation helpers.

## Source evidence

Every certificate binds to:

- replay trade and ticket identity;
- canonical provider signal used for the management trigger;
- policy snapshot;
- market-tick Parquet SHA-256 and verified sidecar;
- conversion-tick Parquet SHA-256 and verified sidecar;
- broker-money contract SHA-256;
- candidate result fingerprint;
- oracle result fingerprint.

A changed source invalidates the certificate.

Every cached market and conversion day must also prove its acquisition:

- one full UTC-day MT5 query;
- two independent half-day MT5 queries;
- identical ordered `(time_utc, bid, ask)` row counts and SHA-256 digests;
- half-open UTC storage boundaries, even though MT5 range endpoints are
  inclusive;
- immutable Parquet and sidecar fingerprints.

Matching file hashes alone are insufficient: the independent oracle
recalculates the ordered quote-stream digest from Parquet.

## Time and event rules

- All timestamps are timezone-aware UTC.
- MT5 entry time, price, volume, and ticket are immutable.
- Confirmed or snapshot MT5 levels are usable. Requested-only levels are not.
- A level becomes active at its confirmed timestamp.
- A provider management action becomes available at its observed timestamp.
- BUY positions close against Bid.
- SELL positions close against Ask.
- Ticks are stably ordered by UTC time, preserving source order for equal
  timestamps.
- Multiple quotes with one millisecond are allowed only when every possible
  ordering gives the same certified outcome.
- A price touch and management action at an indistinguishable timestamp are
  allowed only when both orderings produce the same close price and money.
- If SL and TP can both trigger at an indistinguishable instant, the ticket is
  blocked.
- Conflicting level values at one timestamp block the ticket.
- Naive timestamps, crossed quotes, non-positive quotes, invalid numbers,
  unsupported directions, missing coverage, or an incomplete horizon block
  the ticket.

## Execution rules

- `follow_actual` uses the observed MT5 result and is still checked for exact
  ticket identity and money.
- Counterfactual policies keep actual MT5 entries and volumes.
- Leg order is independently derived from the causal TP active at the
  management trigger.
- Observed BE reassignments are removed before applying a counterfactual
  policy. An event whose source contains BE or whose SL equals the entry is a
  BE event.
- `close_now` closes at the first executable quote at or after the trigger.
- `move_to_be` adds an SL at the actual ticket entry.
- `runner` keeps the non-BE confirmed SL history.
- TP history remains confirmed MT5 history for the executed-MT5 universe.
- No implicit volume fallback is allowed.
- No implicit unit-value calibration is allowed.

## Money rules

The oracle uses Decimal arithmetic and the frozen broker contract.

For XAUUSD calculation mode 4:

`profit_currency_pnl = directional_price_delta * contract_size * volume`

Conversion uses the latest causal EURUSD quote:

- positive USD profit converted with EURUSD Ask;
- negative USD profit converted with EURUSD Bid;
- the same freshness and interval limits as the frozen contract;
- ROUND_HALF_UP to account currency digits.

Unsupported commission, fee, swap, overnight holding, symbol metadata, or
conversion evidence blocks the result.

## Per-ticket proof

Each proof records:

- signal, policy, and ticket;
- immutable entry;
- chosen policy action;
- causal trigger and active SL/TP events;
- quote side;
- first-touch tick index, timestamp, Bid, Ask, and source hash;
- close reason, level, price, and timestamp;
- profit-currency formula;
- conversion quote and account-currency rounding;
- candidate output;
- oracle output;
- exact comparisons and blockers.

Proofs are sorted deterministically. Their canonical JSON has a SHA-256.

## Independent comparison

The certifier compares exact ticket sets and, per ticket:

- entry timestamp;
- entry price;
- volume;
- policy action;
- close reason;
- close timestamp;
- close price;
- touch quote side and touch-side price;
- profit-currency P&L before conversion;
- account P&L to the cent.
- complete conversion-quote evidence;
- the complete money formula and rounding contract.

Float comparison is allowed only for prices at one tenth of the instrument
tick size. Money must match exactly after account-currency quantization.

## Adversarial tests

The suite must prove that certification fails after each mutation:

- BUY/SELL flip;
- Bid/Ask swap;
- one-millisecond trigger shift;
- MT5 time-offset change;
- target shifted by one tick;
- volume change;
- conversion quote change;
- dropped first-touch tick;
- duplicate conflicting tick;
- SL and TP touched at the same instant;
- conflicting level events at one timestamp;
- BE reassignment mislabeled as a TP assignment;
- source artifact changed after its manifest;
- unsupported cost or overnight exposure;
- candidate close reason, time, price, or money changed.

Each test must first fail because the protection does not yet exist, then pass
after implementation.

## Farm integration

Before any policy is evaluated, a data preflight computes the union of:

- UTC days needed by executed MT5 trades;
- UTC days needed by formal provider signals, including unexecuted signals;
- every configured provider-latency scenario;
- market and account-currency conversion caches.

Provider-day horizons use only UTC offsets proved by verified broker cache
contracts. Missing, invalid, or incomplete days abort before the expensive
farm starts. After execution, any day requested by the farm but absent from
the preflight is treated as contract drift and aborts publication.

All semantic inputs, both simulator implementations, acquisition tools,
market ticks, conversion ticks, and sidecars are fingerprinted before the
run and checked again after simulation and provenance generation.

The farm report gains an `independent_certification` summary:

- expected and checked policy-trade rows;
- expected and checked tickets;
- certified, blocked, and mismatched counts;
- proof SHA-256;
- deterministic rerun status;
- blockers.

`conclusions_allowed` additionally requires:

- certification complete;
- zero blocked tickets;
- zero mismatched tickets;
- deterministic proof fingerprint.

Exploratory output may still be written when blocked, but:

- `selected_policy` is null;
- `exploratory_ranking` is empty;
- the report mode is `diagnostic_only`;
- no strategy is described as better or profitable.

## Acceptance

Implementation is complete only when:

1. Unit and adversarial tests pass.
2. The full existing test suite passes.
3. Candidate and oracle agree on the clean executed-MT5 window.
4. Every selected ticket has one deterministic proof.
5. A second identical run produces the same proof fingerprint.
6. Every deliberate mutation is detected.
7. No production push, VM restart, or live-strategy change is part of this
   work without explicit user approval.

## Real-data evidence (2026-07-25)

Clean executed-MT5 window: 2026-07-06 through 2026-07-24.

- 132 executed trades accounted for;
- 131 exact observed replays;
- 1 explicitly identified external intervention (power outage/manual close);
- 22 policies and 2,904 policy-trade rows;
- 13,552 independently certified ticket outcomes;
- 616 actual MT5 money reconciliations;
- zero candidate/oracle mismatches;
- zero blocked executed-MT5 policy rows;
- two full runs with identical proof, result, run, and archive fingerprints.

The policy selector remains blocked by `oos_not_validated`. This evidence
proves deterministic reconstruction for this frozen dataset. It does not
prove that any policy is profitable on unseen data.

Provider-only diagnostics retain explicit blockers when evidence is not
exact. Two unexecuted signals currently reach the broker rollover while the
EURUSD conversion feed has no quote inside the frozen freshness limit. The
system does not invent those account-currency cents.

## Residual limits

No software can prove that the broker's historical database itself contains
every tick that existed in the real market. The dual-query contract detects
query-shape disagreement, truncation, reordering, mutation, and local
corruption; it cannot independently audit the broker's upstream archive.

Likewise, deterministic replay does not remove selection bias, multiple
testing, regime change, latency, or live slippage risk. A policy can be
ranked for deployment only after a predeclared out-of-sample process and
multiple-testing controls. Until then, rankings are exploratory and no
winner is published.
