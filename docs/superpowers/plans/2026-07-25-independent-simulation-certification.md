# Independent Simulation Certification Plan

Status: implementation and clean-window deterministic proof complete locally.
No commit, push, VM update, or live restart has been performed.

## Phase 1: Oracle primitives

1. Add failing tests for strict UTC timestamps, quote validity, duplicate
   timestamp ambiguity, simultaneous SL/TP, gap fills, and Bid/Ask direction.
2. Implement strict tick and event normalization in `simulation_oracle.py`.
3. Add failing money tests for direction, contract size, conversion side,
   freshness, and cent rounding.
4. Implement an independent Decimal money oracle.

## Phase 2: Independent policy replay

1. Add failing tests for MT5-entry immutability, causal TP ordering, provider
   trigger timing, BE removal, close-now, move-to-BE, runner, and EOD close.
2. Implement independent policy planning and ticket replay.
3. Cover missing volume, conflicting level history, unsupported policy, and
   unavailable horizon with fail-closed tests.

## Phase 3: Certification

1. Add failing tests for ticket-set and field mismatches.
2. Implement deterministic proof records and exact candidate/oracle compare.
3. Add mutation tests for every protected input and output.
4. Prove the mutation suite catches deliberately corrupted results.

## Phase 4: Farm gate

1. Add failing farm tests requiring independent certification before ranking.
2. Run the oracle beside the candidate on independently loaded tick evidence.
3. Add the certificate summary and proof fingerprint to provenance.
4. Require certification in `conclusions_allowed` and publication validation.

## Phase 5: Real-data proof

1. [complete] Run both engines on the clean executed-MT5 window.
2. [complete] Inspect every blocker or mismatch; do not suppress one.
3. [complete] Repeat the run and compare proof fingerprints.
4. [complete] Run the full suite and document residual limits.

Verification evidence:

- 1,461 repository tests passed;
- changed Python modules compiled successfully;
- `git diff --check` passed;
- no production push, VM update, bot restart, or live order action occurred.
