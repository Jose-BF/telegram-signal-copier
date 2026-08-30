# Authoritative Shadow Settlement Implementation Plan

## Objective

Repair live shadow continuity and durable recovery, then add a deterministic
post-session settlement that compares all three frozen strategies per channel
without missing signals or presenting incomplete arithmetic as a winner.

## Tasks

1. Add regression tests for active-cohort cursors, idle resets and structured
   continuity failures.
2. Track the last persisted state hash and repair checkpoint/recovery chaining,
   including a narrow legacy migration test.
3. Preserve XAUUSD ticks when historical currency conversion is unavailable;
   carry an explicit money-evidence blocker into candidate state.
4. Build a pure post-session settlement module that reconstructs all registered
   signal-candidate pairs from frozen fingerprints, provider management events
   and complete cached ticks.
5. Extend reporting with complete-matrix counts and separate comparison gates
   from production-adoption gates.
6. Add an on-demand daily/weekly command and a non-fatal post-session watcher
   hook without adding work to bot startup.
7. Validate determinism, the 27-28 August real cohort, focused regressions and
   the full test suite. Review the final diff and deployment impact before any
   push.
