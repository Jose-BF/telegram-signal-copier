# Dubai Investing Iterative Strategy Discovery

**Date:** 2026-08-17

## Objective

Build an offline research loop that repeatedly proposes, simulates, criticizes
and refines Dubai Investing strategies. Its purpose is to discover robust demo
candidates, not to select the largest historical profit or change live trading
automatically.

The loop must answer four questions for every iteration:

1. What rule changed?
2. Which observed failure motivated that change?
3. Did it improve more than one chronological period?
4. Did the improvement survive independent replay and execution stress?

## Safety Boundary

The research package cannot import `executor.py`, `listener.py`, `main.py` or
any module capable of sending MT5 orders. It reads immutable replay evidence
and writes research artifacts outside the live startup pipeline.

No candidate can modify configuration, restart the bot, publish to the VM or
promote itself. Live adoption requires a separate feature-flagged change,
complete tests, an empty-position maintenance window and explicit user
approval.

## Evidence Scope

The initial trustworthy corpus is 2026-07-27 through 2026-08-14:

- 42 Dubai Investing signals have exact tick replay;
- the signals cover 14 trading days;
- three additional signals remain visible but excluded from numerical ranking
  because their evidence is blocked;
- the observed MT5 result for the 42 exact signals is -224.64 EUR.

All current dates have already influenced prior research and therefore cannot
be called permanent holdout evidence. They support chronological development
and retrospective challenge only. The first sessions collected after a
candidate is frozen form the first genuine untouched test.

The existing simple search is a mandatory overfitting regression case. Its
selected rule earned +100.33 EUR on the first 20 signals and then lost
-83.57 EUR on the following 22. A new selector that promotes that unstable
rule without warning fails acceptance.

## Two Confidence Layers

The system keeps two experiments separate:

1. **Observed-entry management:** preserves each MT5 fill time, fill price,
   side and volume. It changes only decisions made after entry and is the
   highest-confidence search universe.
2. **Alternative entry and management:** starts from the causal Telegram
   observation time and verified Bid/Ask ticks. It may test delays, pullbacks,
   confirmations or fewer entries, but it must independently prove that every
   hypothetical fill was executable at that moment.

Results from these layers cannot be merged or compared without their confidence
label. Missing entry evidence blocks only the second layer and never weakens
the observed-entry replay.

## Strategy Building Blocks

A strategy is a small state machine assembled from independently testable
blocks. The first catalog covers:

- entry at the observed signal, after a causal delay, after a pullback, after
  momentum confirmation, or no entry when an explicitly defined causal filter
  rejects it;
- any finite number of positions and any broker-valid lot allocation inside an
  explicit per-run search envelope. The observed 0.04 lots is a comparison
  baseline, never a universal ceiling. The envelope is configurable so a run
  may test less or substantially more exposure without changing engine code;
- equal or unequal volume allocation across provider targets;
- provider targets, close-all at one provider target, fixed basket target,
  partial profit plus runner, and target compression or expansion;
- follow provider BE, ignore BE, delayed BE, price-triggered BE and partial BE;
- fixed loss limit, original provider SL, time-dependent loss reduction and
  basket-level emergency stop;
- fixed time close, inactivity close, profit lock, trailing giveback and
  staged profit protection;
- exact provider close/partial/BE management, selective provider management,
  and independently managed positions after entry;
- re-entry only when causal provider evidence permits it;
- simple observable context such as time of day, direction, spread, recent
  volatility, entry-to-SL distance and entry-to-target geometry.

Context rules receive a strong complexity penalty because 42 signals cannot
support a highly segmented model. Rules may use only values available at their
decision timestamp.

## Iterative Feedback Loop

### Generation Zero

Seed the population with the real Dubai strategy, deliberately simple
baselines and diverse combinations of the strategy blocks. Numerical grids are
generated deterministically from a recorded random seed.

### Simulation

Every candidate runs tick by tick with the verified broker clock, executable
Bid/Ask side, actual or explicitly modeled volume, EUR conversion, spread,
commission and rollover contract. Every eligible signal remains in the
denominator. A blocked result is never converted to zero or silently removed.

### Critique

The critic explains each loss and missed opportunity using measurable path
facts, including:

- profit reached and later returned;
- BE or SL hit before a later favorable move;
- profit taken too early;
- time spent stagnant;
- extra position contribution;
- provider instruction that improved or damaged the result;
- sensitivity to entry timing, spread or slippage;
- excessive dependence on one signal or day.

The critic produces structured failure labels and evidence references. Free
text may summarize them but cannot directly alter a strategy.

### Refinement

The next generation is created by three controlled operations:

- mutate one decision in a promising candidate to attack a measured failure;
- combine compatible blocks from candidates that succeeded for different
  reasons;
- introduce a novel but executable rule when the current population shares an
  unresolved failure.

Each child records its parents, mutation reason and expected behavioral change.
Invalid, duplicate, hindsight-dependent or unnecessarily complex candidates
are rejected before simulation.

### Diversity And Stopping

The archive retains a frontier of profit, drawdown, worst day, stability and
rule simplicity instead of one headline winner. Novelty quotas prevent the
population from collapsing into tiny variations of one strategy.

Raw EUR profit is always accompanied by profit per 0.01 lot, return over
drawdown and worst-case exposure. This prevents the loop from presenting a
larger bet as a better trading rule while still allowing larger or smaller
lotage to be explored as a legitimate strategy dimension.

The loop stops when its compute budget is exhausted or no frontier improves
for a configured number of generations. Repeatedly changing a parameter by an
insignificant amount does not count as progress.

## Chronological Validation

Historical evaluation uses expanding chronological folds:

1. 2026-07-27 through 2026-07-31 develops candidates; 2026-08-04 through
   2026-08-05 challenges them.
2. Data through 2026-08-05 develops candidates; 2026-08-06 through
   2026-08-07 challenges them.
3. Data through 2026-08-07 develops candidates; 2026-08-10 through
   2026-08-12 challenges them.
4. Data through 2026-08-12 develops candidates; 2026-08-13 through
   2026-08-14 challenges them.

The critic and generator can inspect only the development side of a fold. The
challenge result is recorded but is not used to repair that same candidate.
Aggregate selection rewards repeatability across folds, not full-period
profit.

After retrospective selection, one candidate family and its parameter
neighborhood are frozen. Newly collected sessions are appended to an untouched
forward ledger and cannot be used for refinement until that forward test is
formally closed.

## Selection And Rejection

Candidates first pass hard gates:

- complete row accounting and causal evidence;
- exposure inside the run's recorded and configurable search envelope;
- no future information in any decision;
- exact agreement between the fast engine and an independent reference engine;
- deterministic reruns from the same source hashes and random seed;
- no catastrophic deterioration under declared latency, spread and slippage
  stress;
- no result dominated by one exceptional trade or one day.

Survivors are presented as a frontier. The principal measures are net EUR,
maximum realized and floating drawdown, worst signal, worst day, return over
drawdown, profitable chronological folds, daily consistency, profit factor and
parameter-neighborhood stability.

The current 42-signal corpus is insufficient to declare a statistical edge.
Project promotion rules still require at least 200 development trades and 100
untouched validation trades, positive validation expectancy, validation Sharpe
above 0.3, bootstrap significance and no material residual autocorrelation.
Before those gates, the strongest outcome is named `demo_candidate`, never
`profitable_strategy` or `validated_edge`.

## Independent Verification

The best candidates are recalculated by a deliberately slower implementation
that does not share fill or exit-selection code with the fast search engine.
It must match every simulated ticket to account-currency precision and explain
any disagreement before ranking resumes.

Finalists also receive:

- neighboring-parameter tests to detect isolated lucky peaks;
- delayed execution and adverse price tests;
- wider-spread tests;
- day-block bootstrap and result-concentration analysis;
- portfolio-level overlap, floating drawdown and recovery-time reconstruction;
- an MT5 Strategy Tester comparison when the rule can be represented there.

## Outputs

Every run writes an immutable run card containing source hashes, exact signal
universe, exclusions, strategy grammar version, seed, generation count and
engine versions. Human-facing output contains:

- a short comparison with the actual Dubai result;
- the surviving strategies in plain language;
- generation-by-generation improvement and rejection reasons;
- daily and signal-level results;
- equity and floating-drawdown charts;
- one visual replay for representative wins, losses and changed outcomes;
- explicit confidence status and remaining evidence requirements.

The complete candidate population is stored in compact columnar form. Only
frontier candidates and diagnostics are expanded into JSON, avoiding enormous
repository files and slow telemetry publication.

## Acceptance

The research loop is ready when:

1. it reproduces all 42 exact Dubai paths and keeps the three blocked paths
   visible;
2. tests prove causal decisions, exposure limits and chronological isolation;
3. the fast and independent engines agree to account-currency precision;
4. the known +100.33/-83.57 overfit case is rejected or prominently labeled;
5. two identical runs produce the same archive and ranking;
6. at least three genuinely different strategy families complete the feedback
   loop;
7. reports distinguish retrospective candidates from untouched forward proof;
8. no live module, VM process or production configuration changes.
