# Gold Signals Zone Lifecycle Design

## Goal

Extend the existing Gold Signals interpreter so the bot can execute the new
zone-based format on the demo account without changing the established
`BUY/SELL NOW` path. Every provider transition and every MT5 decision must
remain causally replayable.

## Evidence

The retained messages from 2026-07-29 through 2026-08-04 contain 53 parseable
zone-plan roots. They include 25 explicit `Active` transitions, 34
`Approaching` transitions, four explicit re-entry cases, plans that reached a
target without an `Active` message, plans rearmed after `Left without us`, and
multi-zone session maps without trade-level TP/SL instructions.

This evidence rules out both an Active-only implementation and opening every
zone immediately when it is published.

## Compatibility Contract

- Existing immediate `BUY/SELL NOW` messages keep their current parser,
  execution gate, scale-out configuration, level interpretation and MT5
  management.
- The zone lifecycle feeds the same Canal 2 order-opening function. It does not
  implement a second trading strategy or a second MT5 executor.
- A Telegram message may create at most one exposure generation. Explicit
  re-entry messages create a new generation with their own message identity.
- Session maps and discretionary market commentary are recorded but never
  converted into an order unless they become a complete formal plan.

## Formal Zone Plan

A plan preserves:

- root Telegram message and all reply aliases;
- direction;
- one or more normalized ranges;
- ordered numeric targets and whether an `Open` runner was published;
- stop loss;
- provider creation, edit and transition timestamps;
- status, expiry and entry generations;
- the exact trigger message and MT5 tick used by each entry.

A plan is executable only when it has one direction, exactly one range, at
least one numeric target and one stop loss. Multi-zone maps without a shared
risk contract remain observation-only.

## Lifecycle

The versioned states are:

1. `draft`: a partial plan exists but TP/SL evidence is incomplete.
2. `armed`: the complete formal plan can trigger on a market touch.
3. `approaching`: provider says price is approaching; no order is created.
4. `activation_pending`: provider explicitly activates an incomplete plan;
   execution waits for the normal Telegram edits to complete it.
5. `triggered`: one entry generation has been opened.
6. `missed`: provider says price left without an entry.
7. `rearmed`: provider says the zone remains valid.
8. `invalidated` or `expired`: no new first-touch entry is allowed.

Every follow-up message becomes an alias of the same plan. This allows a reply
to a reply to resolve transitively instead of stopping after one Telegram hop.

## Entry Rules

### First Touch

An armed plan opens once on the first new MT5 tick whose executable side is in
the published range:

- BUY uses Ask;
- SELL uses Bid.

The monitor evaluates each distinct MT5 tick once and records Bid, Ask,
`time_msc`, range and trigger reason. A jump that never prints inside the range
is not called a touch.

### Explicit Activation

`Active`, `You can enter` and equivalent direct activation language opens the
plan immediately if it has not already opened. Explicit activation is the
provider's authority, so it may open outside the range; the deviation from the
range is recorded. If levels are still incomplete, activation remains pending
and fires once the plan becomes complete.

### Re-entry

`I am re-entering` and equivalent direct language creates one new entry
generation from the referenced formal plan. It uses the re-entry message ID,
the current market price and the plan's latest confirmed TP/SL. `Do not
re-enter` disarms further re-entry but does not close an existing basket.

### Validity

Formal plans expire 24 hours after their latest explicit validity statement.
`Still valid`, `all zones remain valid for Asia` and overnight-validity
messages extend the deadline. Explicit invalidation prevents future entries.
A restart restores only lifecycle schema v2 plans; legacy observation-only
plans cannot become live orders retroactively.

## Management Routing

Once a plan has a live entry generation, ordinary TP/SL/close/BE management is
routed to that Signal through the existing classifier and executor. If several
generations are simultaneously open and the reply chain does not identify one,
the bot records an ambiguity and requests human review rather than guessing.

Provider alternatives are mutually exclusive. For `close profit OR set BE`:

- positive live basket P/L selects `CLOSE_ALL`;
- zero or negative live basket P/L selects exact per-ticket BE;
- the two actions can never execute for the same message.

## Persistence And Replay

The journal records plan creation, every merged edit, transition, alias,
expiration, trigger claim, trigger tick, order result and entry generation.
Restoration reconstructs aliases, current state, consumed first touches and
re-entry generations. Existing replay fields retain the real MT5 ticket, fill,
volume and level history; zone metadata is additional causal evidence.

## Incremental Analysis

`tools/analyze_new_logs.py` remains the fast append-only reader. Its compact
summary gains zone lifecycle counters, unresolved transitions and entry trigger
counts. Daily reviews consume the new slice; periodic full-corpus audits still
verify that the cursor and derived evidence have not drifted.

## Failure Handling

- MT5 unavailable: keep the plan armed and log the failed trigger attempt; do
  not mark it consumed until a fill is confirmed.
- Duplicate Telegram delivery or edit: merge idempotently and never create a
  second generation.
- Missing levels after explicit activation: wait for edits and issue a review
  warning after the existing correction timeout rather than inventing a trade.
- Restart after a confirmed fill: the durable MT5/state identity prevents a
  repeat entry.
- Unknown wording: preserve the raw message and lifecycle context for review;
  it cannot mutate live exposure by itself.

## Verification

Tests must cover the real message sequences from the retained corpus, including
first touch, Active before levels, full-plan Active replies, recursive replies,
missed/rearmed plans, explicit re-entry, multi-zone observation-only maps,
restart restoration, duplicate delivery and mutually exclusive close/BE.
The complete repository test suite must pass before publication.
