# Passive Execution Latency Telemetry

## Objective

Measure the technical path from a Telegram or internal decision to the MT5
broker response without adding network calls, disk waits, or trading rules to
the live order path.

## Contract

- Keep the deployed Gold 555 economic parameters unchanged.
- Timestamp the existing `mt5.order_send` call immediately before and after it
  with monotonic nanoseconds.
- Record pre-broker, order-send and post-broker stages in the existing causal
  `mt5_action_attempt` event.
- Derive adverse slippage from the already available requested and fill prices.
- Read `ping_last` and `retransmission` only from the MT5 connection monitor's
  existing background `terminal_info` snapshot.
- Join decision and attempt clocks only inside the same process session.
- Publish p50, p90, p95, p99 and maximum values. Do not promote empirical
  simulation scenarios until at least 30 successful market samples exist.

## Safety

No extra call is made to Telegram, MT5, the broker, or disk while an order is
being submitted. Existing append-only runtime telemetry carries the added
fields. Missing metrics fail open for trading and remain absent from analysis.
