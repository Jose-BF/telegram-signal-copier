# Session-Aware Replay And Level Drift Design

Date: 2026-07-16
Status: approved for implementation

## Goal

Prevent quote-only ticks outside Vantage's XAUUSD trading session from being
treated as executable prices, and record any live MT5 SL/TP mutation that
cannot be attributed to bot state or a pending bot action.

These changes improve replay evidence only. They must not place, modify, or
close an order and must not alter live strategy decisions.

## Evidence Behind The Change

The SELL opened on 2026-07-10 retained three positions over the weekend. The
tick cache starts quoting again at 2026-07-12 22:00 UTC, but Vantage publishes
standard XAUUSD hours beginning Monday at 01:01 broker-server time. The
verified cache offset was UTC+3, so the first executable instant was 22:01
UTC. The current validator incorrectly used the quote-only 22:00 ticks and
reported three early TP touches.

The SELL opened on 2026-07-09 stopped producing bot telemetry at 14:31 UTC.
Two MT5 positions then closed by SL near entry at 14:54:48, and the bot
restarted at 14:54:57. Telegram did not publish its BE instruction until
15:00:26. The actual P&L is known from MT5 deals, but the SL mutation time is
not present and cannot honestly be reconstructed.

## Broker Session Contract

Create an offline-only Vantage standard XAUUSD session contract:

- contract id: `vantage_xauusd_standard_v1`;
- server Monday through Thursday: `[01:01, 23:58)`;
- server Friday: `[01:01, 23:57)`;
- server Saturday and Sunday: closed;
- UTC conversion uses each verified tick sidecar's
  `time_evidence.utc_offset_seconds`, never the workstation timezone.

The schedule is explicit broker evidence, not a timing tolerance. Raw Parquet
files remain unchanged. `ReplayTickFrameCache` filters only the in-memory
frames used by observed and provider-first replay and records the number of
discarded quote-only ticks. Missing server-offset evidence fails closed.

Observed replay and readiness artifacts must publish and verify the session
contract id. This prevents an older exact report, generated without the
session rule, from being accepted as current evidence.

## Unattributed Level Changes

The live auditor already polls open MT5 positions every five seconds. Extend
that read-only audit as follows:

- include each open ticket's actual SL and TP in the existing periodic audit
  snapshot, without adding extra snapshot lines;
- remember the last observed SL/TP pair per open ticket;
- when a later pair changes, compare it with confirmed per-ticket bot state
  and any matching pending `MODIFY_SLTP` action;
- if neither explains the new value, emit one
  `mt5_level_change_unattributed` event and one warning anomaly containing the
  previous, current and expected levels;
- retain the previous and current observation timestamps as an uncertainty
  window, and carry that evidence through the ledger and replay ticket history;
- never modify MT5 or silently adopt the external value.

Add `tp_by_ticket` beside the existing `sl_by_ticket`. The pending-action queue
updates both maps only after MT5 confirms the modification, so legitimate bot
changes are not mislabeled as external.

The first observation after startup establishes a baseline and produces no
warning. A level change while the bot is offline remains unknowable; the
subsequent replay must retain a named `missing_sl_transition_evidence` blocker
instead of inventing a timestamp.

An online unattributed change is stored as `observed_unattributed`, not as a
confirmed level transition. The validator names its observation window but
does not use the end of that window as a fabricated exact activation time.

## Replay Semantics

- MT5 deals remain the authority for actual fill price and account P&L.
- Tradable ticks remain the authority for causal first touch.
- A broker SL close unsupported by the recorded active SL history remains a
  mismatch with a precise missing-transition blocker.
- The corresponding provider signal remains present in provider-first
  simulation and follows Telegram plus tradable ticks; it is not deleted
  because the live account received an external intervention.

## Verification

- A Sunday 22:00 UTC quote with UTC+3 sidecar evidence is removed, while the
  first tick at or after 22:01 remains.
- A weekend-held TP validates against the first tradable tick rather than the
  first quote-only tick.
- Existing intraday exact replay remains unchanged.
- A first live level observation is silent.
- An unexplained later SL or TP change emits exactly one forensic event.
- A confirmed or pending bot modification emits no false warning.
- Focused tests and the complete repository suite pass.

## Source

Vantage publishes standard gold trading hours as Monday-Thursday 01:01-23:58
and Friday 01:01-23:57. The live MT5 platform remains the final reference for
the account: https://www.vantagemarkets.com/en/commodities-trading/gold-trading/
