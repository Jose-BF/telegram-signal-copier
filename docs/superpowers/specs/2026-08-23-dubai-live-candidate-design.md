# Dubai Balanced V1 Live Demo Design

## Objective

Run the frozen retrospective Dubai Investing candidate on the Vantage demo
account without changing Gold Signals or claiming a validated trading edge.
The live implementation must preserve the exact candidate rule, expose its
identity in every session and remain reversible through one configuration
switch.

## Frozen Rule

The strategy identity is
`32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631`.
For every new Dubai Investing signal:

- open 0.01 lots immediately at market;
- for 15 minutes from the bot-observed Telegram signal, open 0.04 lots after
  an adverse move of 4 XAUUSD dollars;
- during the same window, open another 0.04 lots after an adverse move of
  8 XAUUSD dollars from the first fill;
- install no initial ticket TP, SL or automatic break-even;
- close every open ticket when confirmed aggregate account-currency P/L is
  at or below -25 EUR;
- arm protection when aggregate P/L first reaches +10 EUR, then close every
  ticket after a 2 EUR giveback from the best confirmed aggregate P/L;
- after 40 minutes from the first fill, close every ticket only when aggregate
  P/L is zero or negative;
- honour explicit provider close instructions, while recording but not
  applying provider TP, SL and break-even changes.

The first fill and both ladder levels use the broker side appropriate to the
direction: Ask for BUY and Bid for SELL. The live executor records the actual
broker fill rather than treating the trigger quote as the fill. Entry and
aggregate-exit decisions are evaluated on every fresh MT5 broker tick; periodic
journal snapshots never control the decision cadence.

## Runtime Boundary

The candidate is enabled only for `canal1` and only while MT5 reports a demo
account. A startup check blocks the bot if the candidate is enabled on a real
or unverifiable account. `canal2` retains its current entry, level and
management behavior.

The candidate is selected by an explicit strategy field on each `Signal`.
This avoids making global listener rules depend on the current configuration
after a restart. The strategy contract, fingerprint, planned legs, trigger
levels, expiry, fills, skipped management actions and exit reason are written
to the journal.

## Recovery

After a restart, an open Dubai basket is reconstructed from MT5 and journal
evidence. The first fill remains the ladder anchor, already-filled candidate
legs are retained, and only still-missing levels inside the original 15-minute
window can open. The aggregate guard restores its armed state and peak. An
expired ladder is never restarted.

## Safety And Rollback

`STRATEGY_C1_BALANCED_V1_ENABLED=0` restores the previous Dubai scale-out and
basket guard behavior without reverting code. Candidate exposure is capped at
0.09 lots per signal. A fill failure keeps the level pending for a bounded
retry path and cannot duplicate a filled leg.

This is a demo forward experiment. The retrospective +269.99 EUR result is a
hypothesis selected on already-seen data. It is not evidence of future profit.
The rule remains frozen while collecting the first 15, 45 and 100 untouched
Dubai signals. Its aggregate protection is process-side and deliberately does
not install a broker SL. If the bot, MT5 terminal or VM stops, the candidate
cannot enforce its loss cap; this is another reason it cannot run outside demo.

## Verification

Tests must prove contract fingerprint parity with the research genome,
directional ladder levels, volume allocation, expiry, no initial SL/TP,
dynamic giveback, loss-only time exit, provider-action filtering, restart
recovery, channel isolation, demo-only enforcement and exposure limits. The
full repository suite must pass before any commit is offered for deployment.
