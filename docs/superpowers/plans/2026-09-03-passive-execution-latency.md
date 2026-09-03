# Passive Execution Latency Implementation

1. Add failing tests for broker-stage timestamps, exception paths and
   background terminal network fields.
2. Extend the existing MT5 causal attempt record around `order_send`.
3. Summarize technical stages and slippage with robust percentiles.
4. Surface the summary through the existing incremental log analysis command.
5. Prove Gold 555 contract and lifecycle compatibility, run the full suite,
   inspect the VM for open positions, then publish only when restart is safe.
