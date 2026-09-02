# Gold Signals NOW Iterative Strategy Farm Design

## Goal

Build a deterministic offline research engine for formal Gold Signals
`BUY/SELL NOW` signals. It must explore materially different entry, sizing,
protection, management and exit behaviours, explain why each iteration changed,
and reject results that cannot be reproduced from causal Telegram, broker tick
and account-money evidence.

The farm discovers demo candidates. It does not alter the live bot, promote a
winner automatically or describe retrospective profit as a validated edge.

## Scope

- Include formal Canal 2 `BUY NOW` and `SELL NOW` signal roots and their causal
  management timeline.
- Keep every eligible signal in row accounting, including unfilled and blocked
  outcomes.
- Keep Gold zone plans outside the first engine. Their observations remain in
  the logs for a later, separately specified study.
- Start from the trustworthy collection boundary of 2026-07-27. The loader
  determines the exact usable count from current evidence; no count is embedded
  in code or documentation as an assumption.
- Preserve both confidence layers: actual MT5 entry management and fully
  alternative causal entry plus management.

## Evidence Contract

One canonical dataset manifest binds the replay trades, tick audit, provider
catalog, raw causal events, XAUUSD Bid/Ask ticks, conversion ticks and broker
money contract by SHA-256. A signal can be simulated only when its required
evidence is complete for the requested confidence layer.

The actual MT5 result is read only from reconciled broker deals. A shadow or
strategy role can never substitute for actual evidence. Missing actual evidence
is named `actual_evidence_missing` and blocks parity and ranking for that row.

The provider's published daily or weekly pips are useful calibration claims,
not broker P/L. When a claim is captured, the scorecard records its source,
period and accounting hypothesis. The optimizer may measure how closely a
causal strategy reproduces the claim, but published pips cannot overwrite
broker truth or become the sole objective.

## Shared Research Core

The proven Dubai iterative package already provides immutable genomes, bounded
feedback, chronological folds, deterministic checkpoints, exact money paths,
an independent scalar oracle, robustness worlds and compact artifacts. Gold
must reuse those mechanisms through channel-neutral path and dataset contracts.

Compatibility aliases preserve all Dubai imports and historical fingerprints.
Generalization must first pass characterization tests proving that every frozen
Dubai fixture and finalist result is unchanged to the account cent.

## Gold Strategy Grammar

The Gold grammar extends the shared strategy genome with independently
composable blocks:

- immediate, delayed, pullback, momentum, adverse-then-reversal or no-entry
  decisions, all expiring after an explicit causal window;
- one to the configured maximum number of legs, arbitrary broker-valid volume
  allocation and simultaneous, adverse or favourable ladders;
- per-leg fixed moves, per-leg vectors, provider targets, one basket target,
  partial closes, runners or no target;
- broker SL at a fixed price distance, account-money basket stop, provider SL,
  monotonic trailing stop or a declared combination with deterministic priority;
- no BE, provider BE, price BE, delayed BE or partial BE;
- fixed profit, peak giveback, staged locks, time exits and non-negative exits;
- exact, close-only, explicit-close-only or ignored provider management;
- simple causal context filters with a complexity penalty.

The search envelope records maximum total lots, leg count, broker volume step,
entry window and path horizon. More lotage can be explored, but normalized
profit, drawdown and configured loss are reported beside raw EUR so leverage
cannot masquerade as a better rule.

## Strategy Semantics

Every genome compiles to the same immutable runtime contract consumed by
historical simulation, prospective shadow and demo execution. The contract
declares entry intents, pending lifetime, target and SL ownership, provider
management, basket protection and terminal rules.

A temporary flat basket is not terminal while a contract still has an eligible
entry intent. The Gold 555 experiment is one genome under this model; it is not
a branch in the engine.

## Feedback Loop

Generation zero combines the provider-following baseline, the current 555 and
c490 experiments, simple controls and structurally diverse strategies. Each
result receives structured diagnostics such as missed entry, excessive adverse
exposure, returned profit, early target, harmful stop, stale holding, damaging
leg, provider-management benefit or cost, and day concentration.

Children can mutate one diagnosed decision, combine compatible blocks or add a
bounded scout from a different family. Every child stores parents, diagnosis,
expected behavioural change and lineage depth. Observationally equivalent
genomes collapse to one behaviour class.

The loop stops on generation, evaluation, wall-clock, no-improvement or lineage
limits. Repeated negligible parameter changes do not count as progress.

## Validation

Chronological folds are generated from complete trading days rather than fixed
row counts. Development data may create candidates; its immediately later
challenge block may evaluate but not repair that candidate. The same-day rows
and overlapping baskets remain together to prevent leakage.

Hard gates require:

- complete row accounting and causal evidence;
- deterministic rerun from the same manifest and seed;
- cent agreement between fast engine and independent scalar oracle;
- correct Ask entry/Bid exit for BUY and Bid entry/Ask exit for SELL;
- broker-valid volume, SL and target levels;
- survival under declared latency, slippage, spread and money-conversion worlds;
- no result dominated by one signal or one trading day;
- positive performance in more than one chronological challenge block.

The available corpus may produce a retrospective demo candidate, but a true
edge still requires the project's minimum development and untouched OOS sample,
bootstrap significance and residual checks. Until then reports say
`retrospective_candidate` or `forward_trial`, never `winner` or `validated`.

## Acceptance

The first complete release must:

1. account for every formal Gold NOW signal since the selected boundary;
2. reproduce the frozen 555 and c490 policies with their exact fingerprints;
3. verify a sample from every strategy family with the independent oracle;
4. reject the known `canal2_2320` live/shadow mismatch until actual lifecycle
   parity is restored;
5. produce deterministic compact run cards, candidate tables and per-signal
   explanations;
6. preserve all Dubai characterization tests and historical cent results;
7. remain unable to import live order modules, restart the bot or change VM
   configuration.

