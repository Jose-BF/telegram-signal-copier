# Gold Signals Strategy Optimization

**Date:** 2026-08-06

## Goal

Determine the most robust way to trade every Gold Signals format before
changing the demo bot. The optimizer covers signal selection, entry, exposure,
management and exit. Zone penetration and DCA are one research phase within
that larger objective, not the final objective themselves.

The primary objective is to maximize verified net account-currency P&L after
all execution costs. A policy is not acceptable if that profit depends on an
unreasonable drawdown, one exceptional day, favorable fills or omitted
signals.

## Global Decision Model

Every provider opportunity is represented by one causal lifecycle regardless
of its message format:

- immediate `BUY/SELL NOW` signals;
- range or zone plans, including `Approaching`, `Active` and `Missed` states;
- re-entry, additional-entry and layering instructions;
- TP, partial close, BE, SL, close-all and invalidation management;
- legacy and new Gold Signals formats while both remain observable.

The optimizer can vary six independent decisions:

1. whether and when a signal becomes tradable;
2. market, limit, confirmation or retest entry trigger;
3. number, placement and volume of entries;
4. per-signal and concurrent portfolio exposure;
5. provider-led, fixed or state-dependent management;
6. individual-leg or whole-basket exit behavior.

Experiments are factorized so the effect of each decision remains measurable.
Only after each family is understood are entry and management policies
combined.

## Evidence Scope

- The official robust collection window begins with causal journal schema v2
  at commit `4a3198c` on 2026-07-26 19:25 UTC. Its first full trading day is
  2026-07-27.
- Modeled zone-entry research uses complete textual Gold Signals plans from
  2026-07-29 onward. Media-only plans remain blocked until their levels are
  captured independently.
- The invalid 2026-08-03 tick-clock contract remains in the denominator but
  cannot contribute a simulated result until repaired.
- The 2026-08-04 and 2026-08-05 data are engineering calibration only. They
  may verify behavior, but they cannot select a profitable policy by
  themselves. Subsequent untouched days provide forward/OOS evidence.

## Safety Boundary

The research engine is offline and cannot import or call live order modules.
Live behavior, configuration and VM processes remain unchanged until a
candidate passes the evidence gates and receives explicit user approval.

Every policy keeps total planned exposure at or below 0.05 lots per signal.
Changing lot size is a separate experiment and is excluded from this phase.

## Causal Zone Reconstruction

Each plan is reconstructed from its ordered Telegram revisions. A value can be
used only after it was observed. The timeline contains:

- direction, range, TP and SL revisions;
- `Approaching`, `Active`, `Missed`, `Still valid`, re-entry and invalidation;
- provider progress, TP, SL, close and BE messages;
- exact Bid/Ask ticks and their verified broker-clock contract.

A zone becomes eligible only after one direction, one range, at least one TP
and one SL are known. Future edits never leak into an earlier decision.
Provider progress or TP evidence completes an unfilled opportunity so the
simulator cannot enter it hours later. Explicit revalidation may create a new
eligible generation.

## Zone Entry Policy Families

The first experiment for the new zone format varies only entry behavior and
holds exits constant:

1. `current_live_zone_trigger`: current baseline, five 0.01 market legs on
   first touch or the provider's explicit `Active` trigger.
2. `all_first_touch_causal_expiry`: five 0.01 market legs only on first touch.
3. `all_provider_active`: five 0.01 market legs only after explicit `Active`.
4. `one_first_touch`: one 0.01 leg, no deeper entries.
5. `one_provider_active`: one 0.01 leg only after explicit `Active`.
6. `one_plus_four_equal`: one first-touch leg and four 0.01 levels distributed
   evenly toward the favorable edge.
7. `five_equal_limits`: five equal 0.01 pending levels across the full zone.
8. `best_half_ladder`: five 0.01 levels restricted to the favorable half.
9. `mid_and_best`: entries only at midpoint and favorable edge, with declared
   volume allocation totaling no more than 0.05.

BUY levels progress from the upper boundary toward the lower boundary. SELL
levels progress from the lower boundary toward the upper boundary. A virtual
limit fills only when the executable Ask for BUY or Bid for SELL causally
crosses its level. Unfilled legs remain visible and contribute zero P&L, not a
missing row.

## Zone And DCA Diagnostics

The simulator answers the zone question before optimizing a policy. For every
complete plan it measures whether price reached 0%, 20%, 40%, 60%, 80% and
100% of the zone, the first-touch time for each depth and whether price reached
TP or SL before and after that touch. Plans that never touch the zone remain in
the sample.

Each additional DCA leg is evaluated both as part of the final basket and by
its incremental contribution. Reports show:

- conditional fill probability for every layer;
- improvement in volume-weighted average entry;
- added maximum adverse exposure and margin usage;
- incremental realized P&L and drawdown versus the same policy without that
  layer;
- opportunity cost from unfilled legs and missed moves;
- sensitivity to spread, latency and slippage.

Policies are compared at equal maximum planned volume and also with
risk-normalized results. This prevents a five-leg policy from appearing better
merely because it risked five times more capital.

## Management Policy Families

After entry policies are compared under one common provider TP/SL baseline,
management is varied independently:

- provider TP scale-out;
- all filled legs close at TP1 or TP2;
- provider management messages exactly as observed;
- no BE, BE after TP1 and BE only on explicit provider instruction;
- close-positive-or-BE using the simulated basket's actual state;
- fixed basket loss caps and floating-profit locks;
- cancel unfilled legs after TP1, provider progress, invalidation or expiry.

Entry and management are tested in stages before combinations are allowed.
This prevents a large search from hiding which decision created an apparent
improvement.

## Required Outputs

For every plan and policy the report records:

- eligibility or a named blocker;
- zone touch, maximum penetration percentage and time spent in the zone;
- planned, filled and unfilled legs with exact tick and price;
- average fill, distance to SL, realized exit and exit reason;
- account-currency P&L when the money contract is verified;
- maximum floating loss/profit, giveback and holding time;
- assumptions, source hashes and policy fingerprint.

Aggregate reports include fill rates by depth, expectancy, profit factor,
maximum drawdown, worst basket, exposure overlap and results by day. Blocked
plans remain in the denominator.

## Search And Validation

The engine runs deterministic, factorized experiments rather than an
unbounded parameter grid. Candidate policies must:

1. reproduce the current first-touch baseline where observed MT5 evidence
   exists;
2. produce identical output on repeated runs with the same fingerprint;
3. remain positive under base and adverse latency/slippage scenarios;
4. improve more than one day and not depend on one outlier;
5. pass untouched forward/OOS days before live promotion;
6. be independently checked in MT5 Strategy Tester where the modeled order
   type can be represented.

With the current sample, results are exploratory scenarios. No policy may be
called profitable or selected automatically.

Policies are ranked by net P&L only after hard risk and evidence gates pass.
The report also exposes maximum drawdown, worst day, worst basket, return over
drawdown, fill robustness and performance by signal format. It presents a
Pareto frontier rather than hiding risk behind one combined score.

## Live Promotion

The natural first candidate is `one_plus_four_equal`, because it preserves a
small first-touch position while improving average entry as the zone fills.
It is not deployed merely because it is the preferred design.

Promotion requires a separate feature flag, demo-only soak, complete order
and cancellation logging, a clean VM with no open positions, full tests and
explicit user approval. Immediate `BUY/SELL NOW` signals and Dubai Investing
remain unchanged in this phase.

## First Calibration Run

The deterministic 2026-07-29 through 2026-08-05 run retained 59 zone records:
41 had complete textual plans and 27 also had a valid tick contract. The 18
incomplete plans and 14 complete plans on the invalid 2026-08-03 tick clock
remained visible as blockers. Nine policies therefore produced all 531
expected rows, including 288 blocked rows.

The independent depth auditor checked all 243 eligible policy rows with zero
disagreements. All five observed MT5 zone baskets and their 25 fills were
verified directly against ticks. The zero-latency modeled baseline matched
four of those five baskets; the remaining basket is retained as observed
broker-latency evidence rather than being mislabeled as a tick failure.

At equal 0.05-lot planned exposure and common provider-level exits, the
current live trigger modeled `-125.28 EUR` across the 27 tick-valid complete
plans. `one_plus_four_equal` modeled `-39.41 EUR` with a lower maximum
drawdown (`74.11 EUR` versus `206.27 EUR`). On the five opportunities actually
executed by the bot, the same modeled comparison was `-194.00 EUR` versus
`-74.11 EUR`; actual reconciled live MT5 P&L was `-167.70 EUR`, shown only as
context because live fills and management were not the common modeled exit
contract.

This is evidence of improvement over the current entry shape, not evidence of
profitability. `one_plus_four_equal` remained negative, its daily result was
unstable and deeper legs did not add positive aggregate P&L in this small
sample. Selection therefore remains `exploratory_only` pending untouched
forward days and repair or replacement of blocked evidence.

## Acceptance

The research phase is complete when:

1. every robust-window Gold Signals opportunity is assigned a known lifecycle
   or a named blocker;
2. every complete zone has one row per zone-entry policy;
3. fill-depth counts agree with an independent implementation;
4. the baseline reproduces observed immediate and zone entries within declared
   broker execution tolerance;
5. missing media, invalid ticks or ambiguous lifecycle events block visibly;
6. reports clearly separate exact observed replay from modeled strategy
   scenarios;
7. no live module or production file changes as a side effect of a run.
