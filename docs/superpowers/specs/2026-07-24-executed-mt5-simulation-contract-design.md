# Executed MT5 Simulation Contract

**Date:** 2026-07-24

## Goal

Make the strategy farm answer one primary question:

> Starting from the positions that MT5 actually opened, what account-currency
> result would each alternative management policy have produced?

The existing provider-first simulation remains available as a secondary
diagnostic. It must not contribute trades, money, or rankings to the primary
executed-MT5 result.

## Source Authorities

The primary replay uses separate authorities for separate facts:

- MT5 deals: ticket identity, fill time, fill price, volume, realized money.
- Confirmed MT5 position history: SL and TP levels that were actually active.
- Canonical Telegram timeline: when a provider management instruction became
  available to the bot.
- Verified bid/ask tick cache: counterfactual path after an instruction.
- Verified broker money contract: account-currency conversion and costs.
- Replay source manifest: exact ledger, raw event and replay artifact hashes.

Telegram text may trigger a counterfactual management decision, but it must
never replace an observed MT5 entry or silently invent another position in the
primary replay.

## Primary Universe

One replay trade is one MT5-executed signal group from `replay_trades.jsonl`.
For every selected trade and every policy, the farm must emit exactly one row.

Each row must preserve:

- the same ticket set;
- each ticket's MT5 open time;
- each ticket's MT5 open price;
- each ticket's MT5 volume.

Policies may change only management and exit behavior. The initial
`follow_actual` policy must reproduce observed MT5 money exactly.

## Secondary Provider Diagnostic

Formal provider signals without MT5 executions remain useful for finding bot
downtime, missed messages, parsing failures, or hypothetical opportunities.
They remain in `provider_policy_results`, but that section is explicitly
diagnostic and cannot select a strategy.

## Fail-Closed Acceptance

The executed contract fails when:

- a trade/policy row is omitted or duplicated;
- a simulated row changes an MT5 entry fact;
- a policy contains a non-`actual_mt5` entry policy;
- the replay was not built from the current ledger and raw event stream;
- an observed baseline is neither causally exact nor a fully recorded external
  intervention in the executed-MT5 universe;
- account-currency money is not verified;
- a counterfactual needs a Telegram management trigger that is missing.

Broker-contract validity and full run-money reconciliation are independent
gates. A valid conversion formula cannot authorize conclusions while any
selected ticket or policy row remains unpriced or mismatched.

Blocked rows remain visible in the denominator. They are never silently
dropped. A blocked or incomplete primary universe may produce diagnostics but
not a winning strategy.

An external intervention is not promoted to exact replay. It is accepted only
when MT5 provides the immutable entry and realized exit facts required by the
primary question, and it remains explicitly labelled in the audit.

## Report Contract

`strategy_farm.json` declares:

- `primary_universe = "executed_mt5"`;
- `validation.price_path_mode = "executed_mt5_entries"`;
- an `executed_scope` row-accounting summary;
- an `executed_replay_contract` invariant report;
- primary `policies` and `selection` from executed MT5 trades only;
- provider-first results under an explicitly secondary diagnostic section.

## Safety Boundary

This work changes offline analysis modules and tests only. It does not import
the farm from live modules, submit MT5 actions, restart the bot, or change
Telegram interpretation in production.
