# Foundation repair and ordered research

Authorized scope: local foundation repairs first, then Canal 2 discovery, then
Canal 1 separately. No publication, restart, live policy change or promotion.
User authorized Sol for bounded implementation and Astra for critical review.
Subsequent authorization on 2026-09-08 (Madrid): controlled demo activation of
the repaired foundation, only after flat exposure and pending-entry checks.
This does not authorize strategy/lot changes or candidate promotion.
Latest direction on 2026-09-08: operational and data-quality cleanup only for
now. Simulations and profitability research are deferred to a separate phase;
do not start them automatically after closing operational issues.

## Milestones

- [x] Execution identity: preserve frozen strategy IDs; version the complete
  terminal contract, test independent mutations and flat/pending behavior.
- [x] Shadow recovery: retain incompatible evidence, make journal recovery
  repeatable, quarantine unrecoverable cursor cohorts without inventing ticks
  or excluding failed cases from reporting denominators.
- [x] Classifier context: separate provider levels from effective autonomous
  protection and identify the account currency correctly.
- [x] Verify focused regressions, full offline suite and independent review.
- [x] Record local results, remaining live validation and data-quality gates.

Original local repair verification: 3,000 tests passed, 624 warnings, zero failures,
errors or skips in 124.70s (Python 3.14.2, pytest 9.0.3; external dependencies
stubbed, not VM integration). Astra's identified findings were corrected;
the final bounded reader review found no remaining P1/P2 in those two fixes.
Red regressions reproduced terminal identity collisions, incorrect reentry,
lost old states, lost quarantine on restart, report promotion of failed
cohorts, global recovery failure, ambiguous cursors, retry starvation and
management applied before its actual availability. Final JSONL regressions
match direct execution, recovery and settlement at submillisecond boundaries.

Frozen source migration preserves all 24 signal/candidate pairs, positions,
money and old identities across repeated recovery. These old cohorts remain
blocked, not prospectively certified. Final result and code fingerprints:
`runtime_data/foundation_repair_20260907_2046/result_final_reader.json`.
Full-suite evidence: `pytest_full_2.xml` in the same directory. Local findings,
pilot metrics and limitations: `docs/audits/2026-09-07-foundation-repair.md`.

Deployment follow-up: added pending-entry heartbeat v3, strict supervisor
validation and cancellation-safe activity tracking for autonomous entry loops.
Final combined suite: 3025 local tests passed; isolated VM operational suite:
1256 passed across 63 files with Python 3.11.9. Full research tests were not
rerun successfully in the VM because its isolated Numba native DLL failed;
that scope passed locally. The tested 33-file update was committed, pushed and
verified active as `9765758ed84bcdcb5e2ca690ec954a2dce154bc6`.
See `docs/audits/2026-09-08-demo-deployment.md` for the activation gates, retained
evidence and active process identities. The pre-existing telemetry publication
lock was subsequently repaired as recorded below. Prospective certification
remains open.

## Subsequent research gates

Do not start candidate selection until the foundation is verified. Before any
annual replay, inventory exact JSON message/edit availability and broker tick
coverage. A second-resolution publication time is an interval, not a recovered
historical fill. Calibrate observed receipt-to-send and execution latency and
slippage from matched logs/deals, distinguishing send-to-response roundtrip
from actual fill timing; preserve clock uncertainty and missingness,
then freeze causal execution assumptions and untouched validation cohorts.
Use bounded searches and checkpoints, first Canal 2, then Canal 1. Claimed
provider pips are descriptive evidence, not an optimization target or fills.

- [x] Obtain publication authorization and check safe demo update conditions.
- [x] Activate and verify the tested version, fresh v3 heartbeat and connections.
- [x] Repair the pre-existing telemetry publication lock with concurrency checks.
- [x] Verify automatic publication and recovered operational source prefixes.
- [x] Inventory existing exports/caches and explicitly retain missing data.
- [ ] Verify repaired behavior prospectively in demo, including real context.
- [ ] Inventory 2026 messages/edits and ticks, then freeze execution assumptions.
- [ ] Complete calibration beyond the 20-opening, one-day diagnostic pilot.
- [ ] Freeze bounded Canal 2 research and untouched validation, then execute it.
- [ ] After Canal 2, repeat independently for Canal 1; no automatic promotion.

## Safety and completion

Baseline: branch feature/gold-555-live-trial, HEAD b4426e6. Preserve existing
dirty audit/report/deployment safeguards. Root owns contracts/engine/runtime;
Sol owns classifier context; Astra reviews migration and continuity risks.
No repaired local result certifies the running VM. Publication requires fresh
authorization and exposure/restart checks for each later update; prospective
evidence remains a gate even after the verified activation above.

Operational cleanup evidence: `docs/audits/2026-09-08-operational-cleanup.md`.
VM code remains 9765758; no extra main publication/restart was needed. Historical
source gaps and fresh-signal validation remain open, and research remains deferred.

Further cleanup: `docs/audits/2026-09-08-pending-cleanup.md`. A read-only VM cache
inventory recovered 58 additional asset/day files and metadata into a new local
archive: 33 pass the current contract loader and stored ordered-content proof,
25 remain diagnostic-only. Telegram exports/media and new demo signal cycles
are still outstanding. The permanent bounded telemetry publication fix is local
and verified: 3035 tests passed, including all 38 telemetry tests; final independent
review found no new P1/P2 after interrupted-cleanup, partial-index and CRLF
regressions were fixed. Evidence is pinned in
`runtime_data/pending_cleanup_20260908_0014/verification_final.json`.
No commit/push or VM activation of this further fix occurred; no new main
publication is authorized.
