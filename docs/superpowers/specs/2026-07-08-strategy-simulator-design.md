# Strategy Simulator Design

**Goal:** build an auditable simulator for management strategies over provider
signals, so we can test many variants without trusting opaque global numbers.

## Core Contract

The simulator treats Telegram signals as the base map:

- direction
- entry/open prices actually obtained by MT5
- provider TP levels
- provider SL level
- level corrections that are not part of the strategy under test

Management strategy is the variable:

- BE timing or ignoring BE
- partial close interpretation
- floating-profit capture
- trailing/profit lock
- channel-specific variants

No strategy result is valid unless the real trade first passes observed tick
replay. The baseline replay must prove that the logged MT5 close is compatible
with cached bid/ask ticks.

## Mandatory Invariants

- A strategy cannot run when baseline tick replay is missing, blocked or
  mismatched.
- A trade without a strategy-relevant event must remain exactly unchanged.
- `STRATEGY_TIME_STOP_NOTIFY_ONLY=1` means time-stop is not a close rule.
- A `no_be` strategy can only remove BE-caused SL moves; it cannot rewrite TP,
  original SL, entry price, lot size or non-BE SL moves.
- If TP/SL never touches after a strategy change, the simulator may use an
  explicit horizon close only when the chosen strategy policy says so, and the
  result must carry an assumption marker.
- P/L must state its source per ticket:
  - `mt5_actual`: unchanged ticket, exact from MT5.
  - `ticket_mt5_calibrated`: simulated close using that ticket's real MT5 P/L
    calibration.
  - `trade_mt5_calibrated`: simulated close using another ticket in the same
    trade.
  - `global_mt5_calibrated`: simulated close using median calibration from
    selected real MT5 tickets.
  - `default_unit_value`: explicit fallback; not decision-grade.

## First Strategy: no_be

`no_be` answers:

> If the provider/bot moved SL to BE, what would have happened if we ignored
> that BE movement and kept the original SL/TP management?

It keeps:

- actual MT5 entries
- actual lot sizes
- original or non-BE SL updates
- TP history
- bid/ask tick execution rules

It changes:

- removes SL history events whose source is BE/breakeven or whose SL is the
  ticket's open price.

## Decision Gate

A global P/L number is only useful after inspecting:

- blocked trades count
- unchanged trades count
- simulated trades count
- assumptions count
- worst deltas by trade
- per-ticket close reason, close tick and P/L source

If any of these are not visible, the result is not acceptable for strategy
selection.
