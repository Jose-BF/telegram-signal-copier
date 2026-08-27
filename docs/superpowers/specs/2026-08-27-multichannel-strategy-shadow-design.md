# Multichannel Strategy Shadow Design

## Goal

Run three frozen strategy candidates for Dubai Investing and three for Gold
Signals against every eligible future signal and every fresh XAUUSD broker
tick. Only the selected live strategy may submit MT5 orders. Shadow strategies
must produce prospective, auditable decisions that can later be compared with
the positions and money actually observed in MT5.

The feature is an evidence system, not a new optimizer. Candidate parameters
remain frozen during a forward comparison window. Broad searches continue
offline after the session and cannot silently replace a running shadow.

## Scope

- Dubai Investing: every formal Canal 1 entry signal and its resolved provider
  management timeline.
- Gold Signals: formal Canal 2 `BUY/SELL NOW` signals and their resolved
  provider management timeline.
- Gold zone plans remain outside this first shadow cohort.
- The existing live policies and order flow remain unchanged.
- Shadow results are denominated in the verified MT5 account currency, EUR.

## Considered Approaches

### End-of-day replay only

Run all candidates after the trading day from collected ticks. This has no
runtime cost, but it does not prove that a candidate was fixed before its
outcome and makes missing live evidence visible too late. It remains useful as
an independent verifier but is not sufficient on its own.

### In-process, side-effect-free shadow runner (selected)

Fan each normalized Telegram event and each unique broker tick into six small
state machines. The shadow package receives primitive values and cannot import
the executor, pending-action queue or MetaTrader5 order functions. It records
only state transitions. This gives the closest causal alignment with the live
bot while adding negligible work per tick.

### Separate shadow process

A second process could isolate failures more strongly, but it would observe
MT5 and Telegram at different instants and would need another durable transport
and recovery protocol. Those timing differences would weaken the comparison
that this feature is intended to measure.

## Frozen Candidate Cohorts

Every identity below includes an immutable strategy fingerprint. Runtime also
records an execution-contract fingerprint covering quote side, virtual fill
rule, broker contract and money conversion. A result is not comparable when
either fingerprint changes.

### Dubai Investing

1. `dubai_balanced_v1`, fingerprint
   `32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631`.
   This is the live control: market leg `0.01`, adverse legs `0.04/0.04` at
   4 and 8 XAUUSD for 15 minutes, basket stop `-25 EUR`, profit arm `+10
   EUR`, close after a `2 EUR` giveback, and a 40-minute loss-only exit.
2. `dubai_frontloaded_30m_v1`, fingerprint
   `d486f5ce418094e862fe3b58e6ccc14068a136ef7116f8a9a80c347083e6dc1c`.
   It uses six adverse-ladder legs `0.01/0.05/0.01/0.02/0.01/0.02`, a
   4-XAUUSD step for 15 minutes, basket stop `-30 EUR`, profit arm `+10
   EUR`, `8 EUR` giveback and a 30-minute loss-only exit.
3. `dubai_frontloaded_40m_v1`, fingerprint
   `cdee2bdfc53aff748d0b87e1d57301793eeb620a4287916c4494cb6681a070b0`.
   It is identical to candidate 2 except for a 40-minute loss-only exit.

All three use exact resolved provider management. The candidates were selected
because they were exact over the 45-signal research set, remained positive in
all six recorded execution-stress worlds, and represented the three strongest
fully certified results. Those retrospective results are selection evidence,
not forward profit claims.

### Gold Signals

1. `gold_now_555_v1`, fingerprint
   `555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0`.
   This is the live control. It waits for a 1.0 adverse move and a 1.5 reversal,
   then uses `0.04/0.03/0.03/0.03/0.03` adverse-ladder legs, per-fill targets,
   an initial stop 30 XAUUSD from each fill that trails monotonically at the
   same distance, a `+30/-1 EUR` profit lock, a 180-minute non-negative exit
   and explicit provider closes only.
2. `gold_now_b210_v1`, fingerprint
   `b210010f4122b5fc2d5e657c512c8a8e94db81647b4d8fe9b0b95228983b5f58`.
   It enters one `0.01` market leg and up to five further `0.01` legs at
   1-XAUUSD adverse steps for 15 minutes, has no fixed target or break-even,
   uses a `-60 EUR` basket stop, arms at `+30 EUR`, closes after a `10 EUR`
   giveback, applies a 3-minute profit-only time exit and follows exact
   provider management.
3. `gold_now_c490_v1`, research fingerprint
   `c4900550abae98de1500bf5b849072956175fdecda102fad69be9f7975cbf8d6`.
   It uses the versioned runtime entry contract of five immediate `0.01` legs,
   no fixed target, a broker protection calibrated to approximately `-20 EUR`
   per leg, price break-even after a 12-XAUUSD favourable move, a `-100 EUR`
   basket stop, `+10/-8 EUR` profit lock, 40-minute loss-only exit and ignores
   provider management. Its execution-contract fingerprint keeps the live-only
   entry and broker-protection definition distinct from its management-only
   research fingerprint.

These three were selected before observing the new forward cohort. The 555
candidate had the strongest retrospective raw result but lower participation
and greater adverse exposure; b210 had full participation and the strongest
normalized result; c490 is the previously deployed benchmark.

## Runtime Architecture

### Candidate registry

A versioned registry constructs exactly three strategies per channel and
publishes their IDs, strategy fingerprints, execution-contract fingerprints
and complete parameters at startup. It verifies that the configured live
policy is present as the channel control. A mismatch disables only shadow
evaluation and emits one actionable journal event; it never blocks live
trading.

### Pure incremental engine

Each candidate owns virtual signals, virtual positions, realized money,
floating money, favourable/adverse extremes and provider-event cursors. Its
public inputs are immutable normalized entry intents, resolved management
events, unique broker ticks and elapsed UTC time. Its outputs are virtual
decisions and state transitions. The engine has no MT5 or executor dependency.

A virtual market decision fills on the first subsequent unique executable
broker tick: Ask for BUY and Bid for SELL. This rule is causal, deterministic
and shared by all six candidates. It does not invent broker slippage. The
control comparison measures the difference between this virtual fill model
and real MT5 execution.

Virtual P/L uses the existing verified broker money contract and account
currency conversion. Missing or stale contract evidence marks the candidate
result incomplete; it never substitutes a price-distance estimate.

### Runtime coordinator

The listener registers all candidates after one formal signal has passed the
normal parser, deduplication and channel checks. The lifecycle loop supplies
each fresh MT5 tick after the live decision path has had priority. Resolved
provider management is fanned out with the same target signal identity used by
the live bot.

The coordinator catches failures per candidate. No shadow exception may
escape into Telegram polling, MT5 supervision or live order handling. It
measures processing time and emits a single degradation event when a candidate
cannot keep up. No Telegram human-review alert is generated for normal shadow
differences.

### Journal and recovery

The existing causal journal records transition-only events:

- cohort registration;
- virtual entry decision and virtual fill;
- ladder fill, level change or provider action;
- guard arm and exit decision;
- final per-signal result;
- checkpoint, recovery, evidence blocker or isolated exception.

Ticks that do not change state are not logged. Every transition includes the
signal ID, candidate identity, both fingerprints, source tick time, bid/ask,
decision inputs and resulting state hash.

After restart, open shadows are reconstructed from the journal. The runner
requests broker tick history from the last processed tick through startup and
replays the gap before accepting current ticks. If exact continuity cannot be
proved, that candidate-signal pair becomes incomplete rather than jumping to
the current price.

## Comparison Contract

The system produces two distinct checks for the active control:

1. **Logic mirror.** Replay the active policy from actual MT5 fills and broker
   closures. Entry count, management decisions, exit reason and account-money
   result must match the real basket. This validates strategy semantics without
   mixing in hypothetical fills.
2. **Causal prediction.** Compare the prospectively recorded virtual control
   against MT5 reality. Entry-price, execution-time and P/L differences measure
   the cost of the virtual fill assumption, latency and slippage.

Alternative candidates are ranked only when the logic mirror passes, all
required Telegram and tick evidence is complete, money conversion is verified
and the candidate was registered before the signal outcome. Otherwise the
report states the blocker and does not print a winner.

The report contains, per signal and per day:

- real MT5 entries, exit reason and net EUR;
- each candidate's virtual entries, exit reason, net EUR, maximum favourable
  EUR and maximum adverse EUR;
- control prediction errors for price, time and money;
- missing-evidence and runtime-health status;
- daily and cumulative totals for each candidate;
- the nine Dubai/Gold candidate pairings, obtained by combining one frozen
  candidate from each channel without changing their individual results.

No automatic promotion follows a daily ranking. Reviews occur at frozen
checkpoints of 15 signals for diagnostics, 45 for a provisional comparison and
100 untouched signals before any edge claim. Parameters cannot be tuned on
signals that remain part of that candidate's forward test.

## Safety Requirements

- Shadow modules cannot import `executor`, the pending-action queue or MT5
  order constants/functions.
- Shadow outputs contain no executable action IDs and cannot enter the durable
  live-action queue.
- Live processing always runs before shadow processing for a shared tick.
- One feature switch disables all shadows without changing live policy.
- One candidate failure disables only that candidate and preserves its partial
  evidence as incomplete.
- Journal growth is bounded by transition-only logging and periodic compact
  checkpoints.
- Deployment requires a demo EUR account, a clean synchronized VM, the full
  test suite passing and no restart while MT5 positions are open.

## Verification

1. Static import tests prove the shadow package has no live execution
   dependency.
2. Unit tests cover every entry, ladder, management, guard and exit transition
   for all six frozen candidates.
3. Golden parity fixtures feed identical historical ticks into the incremental
   engine and the existing offline oracle; entries, exits, reason and money
   must match exactly.
4. Integration tests prove one Telegram signal creates three shadows only in
   its own channel and never calls an MT5 order function.
5. Recovery tests interrupt shadows before entry, during an open basket and
   before exit, then require an identical final result after catch-up.
6. Fault-injection and timing tests prove shadow exceptions and slowdowns do
   not delay or stop live order handling.
7. Report tests block rankings on mirror mismatch, missing ticks, unresolved
   management or money-contract failure.
8. The full repository suite, compile check and clean-diff review pass before
   any deployment is offered.
