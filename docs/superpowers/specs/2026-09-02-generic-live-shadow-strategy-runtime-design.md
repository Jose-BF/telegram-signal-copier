# Generic Live/Shadow Strategy Runtime Design

## Goal

Make a frozen strategy definition portable between deterministic shadow replay
and live MT5 demo execution without adding strategy-specific lifecycle rules to
the bot core. Gold 555 remains an experimental candidate, not a permanent
architectural assumption.

The same strategy identity and fingerprint must describe entries, pending-leg
lifetime, targets, protection, management, and terminal conditions in both
environments. Live and shadow may differ only where the broker necessarily
adds latency, slippage, rejection, or execution costs.

## Existing Problem

The repository already has strategy policies and a side-effect-free shadow
engine, but live lifecycle ownership is split between the listener, position
monitor, independent reconciler, auditor, pending-action queue, and startup
recovery. Some paths understand that a flat basket can still have future entry
legs while others treat `zero MT5 positions` as a terminal signal.

`canal2_2320` exposed this split. The shadow Gold 555 contract retained four
pending adverse legs after the first leg reached its target. The generic live
reconciler finalized the signal after 90 seconds with no open MT5 position, so
the later legs could not execute. Both records carried the same strategy name
but represented different behavior.

## Considered Approaches

### Patch every Gold 555 branch

Add another Gold-specific exception to the reconciler and copy it into every
future close path. This is small now but repeats the exact failure mode and is
rejected.

### Replace the complete live and shadow engines

Run a single state machine for both environments. This offers the strongest
theoretical reuse but would rewrite mature MT5 protection, retry, recovery,
and telemetry code at once. The operational blast radius is too large and the
approach is rejected for this migration.

### Shared strategy contract and generic lifecycle gate (selected)

Keep separate side-effect-free shadow and MT5 execution adapters, but make
both consume one immutable strategy contract. Route every live terminalization
attempt through one generic lifecycle gate. This removes strategy names from
the infrastructure while preserving the proven broker adapter.

## Architecture

### Normalized source events

Telegram parsing produces facts only: entry intent, provider levels,
management instruction, explicit close, edit, or retraction. Parsing does not
decide position count, lot size, targets, or strategy lifetime.

### Frozen strategy contract

Each strategy publishes an immutable ID, fingerprint, scope, entry plan,
protection plan, management rules, and terminal policy. The contract includes
the rule for a temporarily flat basket:

- whether future legs remain eligible after a target or other closure;
- their absolute expiry;
- which explicit events cancel the remaining plan;
- whether a terminal result requires all positions and entry intents to be
  settled.

Gold 555 declares that its adverse legs remain eligible until the original
30-minute window expires unless an explicit supported terminal event occurs.
A strategy that cancels pending legs after the first target must use a new ID
and fingerprint; it cannot silently change Gold 555 history.

### Strategy decision layer

The decision layer consumes normalized source events, broker observations,
and current strategy state. It emits declarative decisions such as open leg,
modify protection, close position, cancel pending intent, keep signal alive,
or finalize signal. Decisions contain no direct MT5 calls.

### Execution adapters

The live adapter translates decisions into durable MT5 actions and records
real fills, rejection, latency, and slippage. The shadow adapter applies the
same contract to exact historical bid/ask ticks and verified money conversion
without importing MT5 order functions.

The adapters may differ in execution evidence, but not in strategy semantics.

### Generic lifecycle gate

One pure lifecycle service decides whether a terminalization request is valid.
It receives strategy state, pending entry intents, open and settled positions,
current UTC time, and a typed terminal cause.

Automatic `flat basket` detection returns `keep_alive` while an eligible entry
intent remains. Explicit provider close, strategy stop, time exit, retraction,
or operator close settles/cancels pending intents before permitting finality.

The listener, lifecycle monitor, reconciler, auditor, pending queue, and
startup recovery must use this service. Infrastructure modules cannot inspect
`gold_555`, `555`, `c490`, or another candidate ID to decide finality.

### State and observability

Every signal records its strategy ID and fingerprint, lifecycle state,
eligible and settled entry legs, terminal cause, and final decision evidence.
Temporary flat periods are explicit states rather than accidental gaps.

Reports use three distinct labels:

- `actual_mt5`: reconciled broker deals;
- `live_logic_mirror`: the strategy applied to actual fills;
- `shadow_prediction`: prospective virtual execution from broker ticks.

`live_control` is a role, never a synonym for `actual_mt5`. Missing actual
evidence blocks parity and ranking.

## Migration

1. Introduce the pure lifecycle contract and tests without changing selected
   strategy behavior.
2. Route automatic position-monitor and reconciler finalization through it.
3. Route auditor, startup recovery, and explicit terminal paths through typed
   causes while preserving their existing MT5 actions.
4. Remove duplicated strategy-specific finality helpers after parity tests
   pass.
5. Add a static test that rejects direct live `status = "closed"` mutations
   outside the lifecycle service, except test fixtures and historical analysis.

The migration is incremental. No deployment occurs while MT5 has open
positions, and each step must preserve restart recovery for existing baskets.

## Verification

### Regression fixture

An integration fixture based on `canal2_2320` must cover:

1. Gold 555 confirms and fills its first leg.
2. The first leg reaches its target and MT5 becomes temporarily flat.
3. Reconciler and auditor checks occur after the 90-second grace period.
4. The signal remains alive until the entry window expires or later legs fill.
5. Live decisions and shadow decisions have the same ordered leg indexes,
   targets, protection rules, and terminal cause.

### Contract tests

- Automatic flat detection cannot finalize a strategy with eligible legs.
- Explicit terminal causes cancel or settle remaining intents exactly once.
- Restart recovery reproduces the same lifecycle state and expiry.
- Every frozen strategy can select its own flat-basket rule without core edits.
- Live and shadow fingerprints fail closed when contracts differ.
- A report with `actual_evidence_missing` cannot print an actual result,
  parity pass, ranking, or winner.

### Operational acceptance

Deployment requires the focused lifecycle suite, all existing Gold 555 and
shadow parity tests, the complete repository suite, a clean synchronized
branch, verified demo account, and zero open MT5 positions. A pre-deployment
report must show exact decision parity for the regression fixture.

## Non-goals

- This change does not claim that Gold 555 is profitable.
- It does not promote a shadow winner automatically.
- It does not combine Telegram interpretation with strategy decisions.
- It does not replace MT5 with a simulated broker.
- It does not make historical strategies inherit new semantics silently.

The outcome is a strategy runtime where a candidate can be simulated, selected
for demo execution, measured, and removed without modifying unrelated bot
infrastructure.
