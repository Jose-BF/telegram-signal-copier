# Research contracts

Read when simulating or searching strategies. Existing engines and their
verification gates remain authoritative; this guide does not enable a new
experimental mode or relax a code gate by changing terminology.

Current priority: prepare the simulation foundation for large strategy searches,
not assess the profitability of the configured live candidate. Its observed
trades are execution controls. See `2026-09-08-simulation-foundation-readiness.md`
for the current acceptance milestones. Conditioned actual-fill controls and
per-decision live-state replay do not establish independent end-to-end replay.

## Choose a declared universe

- Observed reconstruction: preserve actual MT5 deals, costs and interventions.
- Fixed-entry management: strategy_simulator.py changes management over observed
  MT5 entries and confirmed level history. executed_simulation_contract.py
  preserves ticket identity, fill time/price and volume and checks completeness.
  Provider events can trigger a policy but cannot rewrite observed entry facts.
- Changed entries/volume: use a research engine whose contract supports them,
  with explicit capital/exposure, causal entry rules and money assumptions.
  Do not route this through immutable-entry validation or label it an observed
  replay. If support/evidence is missing, state it rather than synthesizing it.
- Provider coverage: provider_trade_spec.py and provider_strategy_simulator.py
  model formal signals independently of MT5 tickets. Preserve unexecuted and
  blocked rows. NOW and zone universes remain distinct.

strategy_farm.py currently uses the executed-MT5 matrix as primary management
evidence and provider-first results as secondary diagnostics. Verify
executed trades x policies and formal signals x policies x latency scenarios.
This current engine boundary is not a universal ban on separate entry research.

The user's current objective is channel-entry-triggered research with our own
management, not mandatory imitation of every provider action or historical bot
fill. See `2026-09-08-first-study-preparation.md`. Provider-first counterfactuals
do not require an observed MT5 execution for each signal; observed accounting
claims still require their own deal reconciliation. Missing provider levels
block policies that depend on those levels, not every independent fixed-rule
policy. Keep the eligible-signal denominator and evidence limitations explicit.

Investment capital remains open. Scale-controlled diagnostics may declare a
reference size without treating it as the user's intended allocation; portfolio
and margin claims need explicit capital scenarios. Do not assume unlimited
capital, rank higher leverage as a better strategy or bypass engine/money gates.
Prefer profit-versus-tail-loss comparisons, including basket and concurrent
exposure, open-equity drawdown and recovery time. Research budgets remain finite.

## Money, provenance and validity

provider_policy_results[*].strategy_value is directional XAUUSD price movement
across virtual legs, not volume-weighted currency P/L. Do not label it euros,
dollars or monetary profit, and do not select a strategy from that metric.

money_mode=verified_account_currency requires verified symbol/account metadata,
causal conversion ticks, commission, swap and reconciliation of observed deals
to currency precision. Keep money_contract_verified separate from
account_currency_money_verified for every selected observed/counterfactual row.
Both are needed for monetary conclusions.

selection.selected_policy remains null while the applicable money gate,
complete executed-trade matrix or untouched OOS validation is open.
Diagnoses and exploratory comparisons can be reported with their limitations;
do not present them as a certified selection.

Observed external intervention can be included in observed MT5 reconstruction;
it cannot make a provider-first counterfactual exact.
broker_money.py handles conversion and rollover. Exact overnight alternatives
require matching broker snapshots and historical conversion ticks.
mql5/Services/BrokerMoneySnapshotService.mq5 is a read-only, account/server/symbol-
bound source of native swap, weekday and clock evidence. Its transactional
installer is tools/install_broker_money_snapshot_service.py; do not install it
as an incidental research step without deployment authorization.

simulation_run_provenance.py identifies runs by selected payloads, policy order,
source hashes, runtime versions and verified tick contracts. Same identity,
same immutable archive; conflicting results fail closed. Machine path, branch
and wall-clock run timestamp are diagnostics, not computational identity.
Detailed include-trades outputs retain first-published path, size and SHA-256;
compact archives retain their strategy_farm.json. Tick hashes do not recreate
deleted Parquet files: state actual retention and retrieval limitations.
Ordered latency assumptions and per-leg volume belong in the fingerprint.

Keep known failures visible and scoped to their affected contract/cohort.
Do not bypass existing fail-closed gates or silently exclude failed baskets.
See shadow-evidence.md for discrepancy repair and prospective claims.

## Dubai iterative research

research/dubai_iterative/ contains:
- contracts.py: immutable grammar, explicit volume/time envelope, finite budgets.
  Observed 0.04 lots is a baseline, not a universal maximum.
- dataset.py: fail-closed replay, tick, conversion and money input loader.
- fast_engine.py: fixed-point Numba engine; oracle.py: independent scalar
  verifier which must not import either simulation engine.
- evolution.py and search.py: diagnosis, deterministic mutation/crossover/scouts,
  normalized Pareto selection, chronological folds and checkpoints.
- refinement.py, robustness.py, statistics.py: sensitivity, equivalence collapse
  and complete-day checks.
- risk.py and portfolio.py: concurrent configured loss and joint equity over
  one canonical market/conversion tape.
- certification.py: chronological finalists, exact engine/oracle money under
  identical assumptions, six-world execution sensitivity and portfolio checks.
- __main__.py: CLI; ignored runtime_data/dubai_strategy_runs holds artifacts.

Engine/oracle equality does not guarantee live fills. Blocks reused during
discovery are retrospective robustness evidence. Imported winners are seeds
with research_seed_only_full_sample_origin_not_oos status, not fresh OOS.
Research may not import live runtime/order modules, change live configuration,
publish to the VM or promote candidates.

Iterative checkpoint identity also binds the implementation and execution
environment through research/iterative_provenance.py. Changed engine code,
relevant dependencies or execution assumptions require a new run; preserve
the previous checkpoint/archive. Old seen sets, populations and fragments
can affect selection even when an archived genome would be evaluated again.
Do not silently resume them under repaired code. Historical results retain
their original implementation status and need applicable re-verification
before they support current comparisons.

## Gold iterative research

research/gold_iterative/ handles formal BUY/SELL NOW. Never add zone plans
through implicit fallback.
- Account for every eligible signal, including blocked/unexecuted ones.
- Keep all baskets of each day together and use complete days in folds.
- Development may diagnose/mutate; later challenge data evaluates only.
  Do not feed challenge outcomes into another mutation and keep calling it OOS.
- Gold 555, c490 and provider baselines use the common genome, scalar,
  fixed-point and independent-oracle paths. No strategy-specific workaround to
  force a match or implicit substitution for the live policy.
- Stream candidates to deterministic Parquet fragments. Resume only if dataset,
  search envelope, operators, seed, implementation, environment and execution
  assumptions match. Fragment metadata must bind the same run identity.
- MT5, tick, money, oracle, chronology and source-manifest failures make the
  applicable run diagnostic_only. Prospective adoption requires frozen,
  untouched forward evidence.
- Verify the exact run with python -m research.gold_iterative verify --run-dir
  <path> before quoting archived results. This checks retained artifact bytes,
  not whether a historical run is valid under the current engine. Preserve
  original certification limits; reuse only unchanged applicable evidence.
- Provider scorecards are accounting hypotheses. Reconstruct their counting,
  extrema and management conventions separately; never equate their pips with
  realizable account money or force fitted parameters to match their claims.

## Iteration boundaries

Fix the question, eligible data, search envelope, cost/risk metrics and finite
budget before the run. Vary hypotheses with observable reasons, save checkpoints,
and stop on completion, exhausted budget or lack of new discriminating evidence.
Keep losses/tail exposure visible even when maximizing return is the objective.
Research results do not authorize a live strategy change.
