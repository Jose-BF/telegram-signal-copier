# Shadow Evidence And Provider Scorecard Plan

**Goal:** Make the strategy comparison independently useful per provider, prove live-control parity instead of permanently blocking it, structure Gold Signals published claims, and improve log observability without modifying authoritative streams.

## Evidence rules

- Keep Telegram, MT5 ledger, tick cache, and telemetry streams append-only.
- Never infer a passing result from missing or ambiguous evidence.
- Separate logical parity from execution slippage and broker money differences.
- Preserve the existing global paired ranking, while adding independent channel verdicts.
- Treat provider summaries as claims to calibrate, never as verified account P&L.
- Do not rotate or truncate `bot_runtime.log` in this change. Only report its size and warn when it crosses a configured threshold.

## Tasks

1. Add bounded failed-anchor diagnostics to replay tick validation and identify the two August 19 failures without widening tolerances.
2. Serialize deterministic per-leg control signatures for shadow results and derive equivalent signatures from reconciled MT5 ledger rows.
3. Compute exact control parity from those signatures and expose execution deltas separately.
4. Add independent `canal1` and `canal2` comparison/ranking verdicts while preserving conservative global pairing rules.
5. Parse Gold Signals daily and weekly summary claims into a separate scorecard, link them to formal signals, and expose every ambiguity/blocker.
6. Add non-destructive runtime log size health reporting and integrate the new derived report into the watcher finalization pipeline.
7. Run focused tests first, then the full suite, rebuild real reports, and deploy only through the safe updater after confirming the VM has no open exposure.

## Verification

- Tests must demonstrate that a blocked or absent channel cannot contaminate the other channel's independent verdict.
- A deliberately changed leg, target, or exit class must fail parity.
- Execution price/time differences alone must be reported but must not change logical parity.
- Provider arithmetic inconsistencies and uncertain periods must remain visibly blocked.
- Log health checks must never mutate, rename, truncate, or publish a replacement for any raw log.
