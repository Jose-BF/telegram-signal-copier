# Certified Counterfactual Portfolio Replay

**Date:** 2026-07-26

## Purpose

Build a replay system that can answer:

> Starting from the complete causal evidence available at the time, what
> would the whole account have done under a different declared strategy?

The system must account for every signal and position. It must distinguish
facts from assumptions and must never publish a policy result when evidence is
missing, contradictory or silently omitted.

This design extends the existing executed-MT5 replay and independent oracle.
It does not replace either one.

## Scope

The system supports two explicitly separate counterfactual modes:

1. `observed_entries`: preserve MT5 position identity, open time, open price
   and volume. Change only management and exits.
2. `modeled_entries`: reconstruct entries that would have changed under the
   policy, including timing, volume, order count and pending-order behavior.

Both modes run on one chronological account timeline so overlapping signals,
margin, equity, daily limits and cross-channel rules are represented.

## Non-Goals

- Proving that any strategy is profitable.
- Claiming an exact broker fill for an order that was never submitted.
- Replacing out-of-sample and multiple-testing controls.
- Changing live trading behavior while the recorder and replay are built.
- Logging every tick into the JSONL event journal.

## Evidence Classes

Every input is labelled as exactly one of:

- `observed`: immutable evidence captured from Telegram, MT5 or the account.
- `derived`: deterministic normalization of observed evidence.
- `modeled`: an explicit counterfactual assumption.
- `missing`: evidence required by the declared policy but unavailable.

Modeled facts may produce a scenario result. Missing or contradictory facts
must block the affected result.

## Causal Identity Chain

Every live action must be traceable through this chain:

```text
message_revision_id
  -> decision_id
  -> action_id
  -> attempt_id
  -> broker order/deal/position
```

Identifiers are immutable and unique within the repository history.
Retries share one `action_id` and receive distinct `attempt_id` values.
Coalesced, superseded, rejected and no-op actions remain visible.

Every event envelope records:

- schema version and event ID;
- UTC wall-clock timestamp;
- process monotonic timestamp;
- bot session and code commit;
- signal, provider and channel identity;
- applicable causal IDs;
- raw source fields and a canonical payload hash.

## Recording Layers

### 1. Causal action journal

Extend the existing journal without changing order behavior.

For each MT5 attempt record:

- requested operation and effective parameters;
- source tick `time_msc`, Bid and Ask;
- current position SL, TP, volume and price;
- symbol stops level, freeze level, point and digits;
- terminal ping and connection/trading state;
- attempt start/end timestamps;
- complete trade result, retcode and external retcode.

### 2. Passive terminal observer

Add a passive MQL5 recorder that never submits or modifies orders. It records:

- `OnTradeTransaction` request, order, deal and position events;
- broker order, deal and position identifiers;
- deal time in milliseconds, fill price, reason, costs and volume;
- compact live tick windows around trade actions;
- account and symbol contract snapshots when their state changes.

The observer writes through a bounded asynchronous path. A recorder failure
raises an alert but cannot block or restart the trading bot.

### 3. Telegram media archive

For every photo, document or sticker revision store:

- Telegram identity and revision timestamp;
- media type, size and stable metadata;
- SHA-256 and immutable local artifact path;
- extraction status and extracted text when applicable.

The raw message event binds to the media hash. Missing media remains explicit.

## Portfolio Replay

The replay engine processes one stable UTC event queue for the whole account.
It represents:

- simultaneous positions from both providers;
- balance, equity, realized and floating P&L;
- margin, free margin and stop-out constraints;
- commissions, fees, swaps and currency conversion;
- daily loss limits and account-wide protection;
- per-provider and cross-provider exposure limits.

Events at the same timestamp use a documented precedence rule. If two valid
orderings produce different outcomes and the source evidence cannot determine
the order, the result is blocked or emitted as separate named scenarios.

## Counterfactual Execution

`observed_entries` is eligible for exact certification because the entry facts
come from MT5.

`modeled_entries` uses three calibrated scenarios:

- `favorable`;
- `base`;
- `adverse`.

Each scenario declares Telegram delay, local processing delay, broker request
delay, slippage, rejected-stop handling and retry behavior. A strategy that is
profitable only in the favorable scenario is not deployment-eligible.

No modeled fill is labelled exact.

## Result States

Every policy-account run finishes in one state:

- `certified`: complete observed evidence and independent engines agree;
- `scenario`: complete declared assumptions and deterministic result;
- `blocked`: named evidence or contract requirement is missing;
- `error`: engines, source artifacts or invariants disagree.

Blocked signals and positions remain in the denominator. No ranking is
published unless row accounting proves that the complete selected universe was
processed.

## Independent Validation

The existing candidate engine and independent oracle remain separate.
MT5 Strategy Tester is a third, secondary execution-path check.

The following gates are mandatory:

1. Reproduce a known day and a known week with the complete observed account
   universe.
2. Re-run twice with identical fingerprints.
3. Compare every ticket, event, close, cost and account-cent result.
4. Mutate one message, tick, timestamp, action, conversion quote and position;
   each mutation must be detected.
5. Remove each new evidence class in turn; the relevant run must block.
6. Prove that candidate and oracle use independent policy and price-path code.
7. Keep MT5 tester discrepancies visible instead of averaging them away.

## Rollout

### Phase 1: Identity and attempt evidence

Add causal IDs and per-attempt execution context to the existing Python
journal. Rebuild the observed replay and require no regression.

### Phase 2: Passive evidence

Introduce the terminal observer and media archive behind disabled-by-default
flags. Measure CPU, disk and event-loop impact before enabling them in demo.

### Phase 3: Account timeline

Build the portfolio scheduler in `follow_actual` mode. It must reproduce the
known account history before any alternative policy is accepted.

### Phase 4: Alternative realities

Add declarative management, entry, sizing and account-risk policies. Begin
with one simple TP2 policy and one portfolio protection policy.

### Phase 5: Strategy research

Run the strategy farm only after deterministic certification. Use fixed
in-sample, out-of-sample and permanent hold-out partitions, then apply
multiple-testing controls.

## Safety

- No live order path imports replay or research code.
- Recording is append-only, bounded and asynchronous.
- Disk limits and rotation are explicit.
- Recorder failure never pauses Telegram or MT5 handling.
- Production activation requires tests, a demo soak and explicit user
  approval.
- Pushing code and restarting the VM remain separate explicit actions.

## Acceptance Criteria

The architecture is ready for strategy research only when:

1. every selected message, signal, action, attempt, deal and position is
   accounted for;
2. the observed-entry replay reproduces the frozen day and week;
3. missing or contradictory evidence always blocks;
4. all scenario assumptions appear in the result artifact;
5. simultaneous signals and account constraints are exercised by tests;
6. deterministic reruns have identical fingerprints;
7. the recorder causes no measurable trading-path latency regression;
8. no result is described as profitable before out-of-sample validation.

## Irreducible Limits

No logger can observe the broker fill of an order that was never sent. No
historical database can prove that it contains every tick originally seen by
the broker. The system therefore certifies observed-entry realities and
reports calibrated ranges for modeled-entry realities.

That distinction is a required feature, not a temporary limitation.
