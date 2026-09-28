# Telegram Signal Copier

Copy authorized Telegram signals to MT5 and evaluate strategies with auditable
evidence. Canal 1 is Dubai Investing; Canal 2 is Gold Signals. Check current
code/configuration and VM evidence before describing the active strategy.

## Scope and authorization

- Preserve user edits, raw logs, sessions and historical research. On resume,
  recover branch/status and known evidence; do not restart completed work.
- Local analysis, scoped fixes and offline tests can proceed within the request.
  Do not send emails or external messages without authorization for that action.
- A push may trigger the VM watcher. Obtain explicit publication authorization
  for the current change; verify update/restart safety and open exposure before
  deploying. If exposure is open or unknown, do not trigger an unapproved restart.
- Research never changes live policy or promotes a candidate automatically.
  Do not change the live 555 to a different simulated interpretation implicitly.
- Make remote work observable through its output, logs or a user-requested
  visible terminal. Respect host window rules; avoid untracked background jobs.
- Finish by distinguishing local changes, commit, push and verified VM version.
  Explicitly state when changes remain unpublished; do not imply local = live.

## Shared process and user visibility

Use `docs/development/2026-09-08-simulation-foundation-readiness.md` as the
shared process map, not just a collection of technical audit results. Before
each meaningful work block, explain in concise Spanish where it fits, why it
is needed, what result would finish it and what follows. At completion, report
what the evidence establishes and what remains open. Announce material changes
of priority or scope; do not let a diagnostic case silently become the goal.

Historical broker sessions are controls for general simulation mechanisms, not
an objective of matching one day to the millisecond. Distinguish engine
validation, historical data admission and later strategy validation. Detailed
new telemetry is not a universal prerequisite for historical counterfactuals;
requirements depend on the policy and available causal inputs. Preserve all
applicable evidence gates and the joint review before a massive search.

## Evidence that must survive every task

- Preserve channel, signal/basket identity, strategy version, source hashes,
  causal message/edit availability, timezone, Bid/Ask, volume and account currency.
- Reconcile observed deals and costs before claiming observed monetary totals.
  Pips, XAUUSD price movement and account-currency P/L are different units.
- Distinguish observed accounting, policy decisions and hypothetical execution.
  Exact accounting from actual fills does not certify alternative fills.
- Preserve missing/blocked cases and reasons; never silently remove them from
  denominators. A discrepancy is a repair incident with a regression case,
  not a sample to discard. Keep certification gates intact.
- Freeze experiments and untouched validation cohorts before selection.
  Data reused in discovery is retrospective evidence, not fresh OOS.
- Keep research offline, results immutable and candidate loops bounded by an
  explicit budget, checkpoint identity and useful stopping condition.

## Read only the relevant reference

- Live, parser, zones, logs or replay preparation:
  `docs/development/runtime-and-replay.md`.
- Strategy search, changed entries/volume, accounting or provenance:
  `docs/development/research-contracts.md`.
- Live/shadow differences, repair or prospective comparison:
  `docs/development/shadow-evidence.md`.

These references preserve detailed contracts. Do not load them all for a
simple question or document edit. The normal log-review entry point is
`python tools/analyze_new_logs.py`; inspect its options for the requested
period. Do not add whole-corpus analysis to a live order path.

## Verification and completion

Use a regression case and focused tests for a narrow behavior fix. Include
integration checks and `python -m pytest -q` for changes to shared execution,
money, replay contracts, persistence, concurrency or deployment safety.
Documentation-only work needs document/structure checks, not a trading suite.

Record command/result and relevant code, inputs and environment. Reuse known
passing evidence when those are unchanged; rerun invalidated or unknown checks.
Do not widen tolerances, replace independent evidence or alter production
settings to make a test pass. State limitations and remaining work honestly.
