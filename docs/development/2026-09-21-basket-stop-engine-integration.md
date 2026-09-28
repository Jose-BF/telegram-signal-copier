# Basket Stop Engine Integration

## What Changed

Local continuation of the basket-stop read extraction. This connects the read
plan to causal engine state and the existing modelled protection transport; it
does not admit the complete Dubai strategy or certify native MT5 valuation.

`BasketReplaySpec.stop_component` is an explicit `DubaiBasketStopComponent`.
It requires canal1, management reads, a protection profile and exclusive native
stop ownership. Generic per-quote stop modifications are disabled for that
component so they cannot remove its installed stop. Other read-driven rules
retain their existing admission gates. Default scalar policy behavior is not
replaced by this opt-in component.

The driver now runs due stop planning before due monetary observation within
the same monitor turn. It does not insert a new DCA observation between them.
Stop checks and money checks retain independent deadlines. The stop helper
enqueues and returns: summary requests do not require every stop ACK first,
although both kinds of work contend for the shared transport.

The engine's explicit read branches now provide:

- POSITIONS: sampled entry, volume, profit and installed SL/TP, never pending
  desired levels. Known ticket identity is captured when the plan starts.
- SYMBOL: point/digits from the declared protection profile, identified as
  model metadata rather than an observed native specification.
- PROFIT: each request's own action, entry, hypothetical close and volume,
  using causal FX at that sample. The declared linear-contract hypothesis is
  unrounded, excludes fees/swap/slippage and is not native `order_calc_profit`
  certification. The existing cent-rounded money function is not reused.
- Position deal history remains a separate explicit operation, not a catch-all
  branch that would misinterpret valuation requests.

`basket_stop_requirement` shares the delivered-snapshot classification with
the live listener helper: directional point/2 tolerance, already-protected
status, five-second same-level suppression and force override. The extraction
does not change the live policy's budget or publication status.

The engine records stop application separately from the plan. SL-only intents
merge stronger pending levels and preserve TP at dispatch, not from the old
plan snapshot. Requests, broker installation/rejection, ACK and later observed
protection are different stages. An application can be queued without being
sent; a later dispatch failure must not rewrite that earlier stage as an
installation. Broker protection can execute before its ACK arrives.

## Repairs Found During Integration

Two independently reproduced scheduler issues were repaired with failing tests
before the fixes:

1. A drained stop-read timeout restarted overdue stop checks indefinitely and
   starved the money guard. It now proceeds to the due money phase after drain;
   the timeout remains in the report and still blocks complete certification.
2. The money polling interval silently controlled stop cadence. Both deadlines
   now participate in wakeup/admission. A stop-only turn does not pull a money
   check forward before its own deadline.

Same-clock unfinished read work at the causal-round limit now reports explicit
round-budget exhaustion rather than only a later undrained-event symptom.

## Verification

- Initial BUY/SELL end-to-end tests failed with zero stop plans before the
  driver/engine integration, then passed after connection.
- A combined focused run passed 187 tests before the last four zero-delay
  multileg cases; those four and the two gap/ACK cases then passed separately.
- The new test module contains 38 cases covering BUY/SELL, two/three legs,
  DCA snapshot identity, FX side and sample clocks, missing FX, rejection and
  retry, provider cancellation, native closure during valuation, installed
  protection before ACK, independent deadlines, timeouts, budgets, partial
  queue/dispatch failure, stronger installed levels and TP preservation.
- Independent review rechecked the two scheduler fixes and ran 24 combinations
  of BUY/SELL, terminal timing, cutoff and latency without a new material
  finding. That is scoped evidence, not a full-suite result or live validation.
- Full-suite run and eight fully specified synthetic controls are stored in
  `reports/e5-basket-stop-engine-integration-20260921/`. The initial freeze
  contains 395 sources. The full run ended with **7,025 passed, one failed**,
  no errors/skips and ten existing warnings, in 1020.95 seconds. Sources and
  implementation identity stayed unchanged during that run. Its failing result
  is retained in `verification.json` and `pytest.xml`, not rewritten as green.

### Gateway Test Follow-Up

The sole full-run failure was reproduced standalone: the existing worker-death
test assumed that a new spawned process had entered a call after 0.5 seconds;
the observed state was still PREPARED. That is not evidence that a dispatched
operation was incorrectly recovered.

Only `tests/test_mt5_gateway.py` changed after the full-run freeze. The test now
waits for an explicit child-side entry signal and asserts DISPATCHING from the
store before simulating worker death. Its original recovered-count, UNKNOWN
outcome and original-attempt/no-redelivery assertions remain. Two caller
deadlines exercise this setup. No gateway or trading implementation was changed
to make this test pass.

An intermediate test double used a release Event whose cleanup could hang
after killing its waiter. That owned test process was stopped after checking
its identity, absence of child processes and mock UNKNOWN outcome. The release
Event was removed; the final double remains inside the call until the gateway
terminates its worker. This intermediate unsuccessful attempt is recorded, not
treated as validation.

The entire corrected gateway module passed **30 tests** in 36.38 seconds.
`followup-verification.json` independently checks JUnit counts/hashes, confirms
that only that test source changed, and preserves the unchanged implementation
identity and control hashes. This is full-run evidence plus a scoped test-only
repair/recheck, **not a new all-green full-suite execution**. The full-run JUnit
hash is `3cad8e08cc29b637f9b75150e0878679cfd1ffc3fbf1e0b338d4d310117bd2ec`;
the gateway recheck hash is
`7dd1c2731352f00fa63768a649e32a0374a0fdc875c2896fdb6e44bfd88e1622`.

The eight controls preserve complete input specs, the shared profile, replay
output, read/protection traces and the risk grid. Important results in account
minor units (EUR cents):

| Control | Final Net | Drawdown | Admission |
| --- | ---: | ---: | --- |
| BUY install/confirm | -80 | 80 | Model control only |
| SELL install/confirm | -80 | 80 | Model control only |
| Gap through installed stop | -4080 | 4080 | Model control only |
| Rejected stop, later retry and recovery | -80 | 4080 | Model control only |
| Three legs, zero read delay | -580 | 580 | Model control only |
| Independent poll deadlines | -80 | 80 | Model control only |
| Stop timeout followed by money guard | -4080 | Unknown complete metric | Timeout retained |
| Cutoff during stop read | Open, not final P/L | Unknown complete metric | Incomplete retained |

These numbers are synthetic, not results from the user's trading history.
The gap case intentionally exceeds the EUR25 planned stop budget: fills occur
at the executable quote, not at an artificially capped monetary loss. The
rejection/recovery case demonstrates why final P/L cannot establish path parity.

## Still Open

- Native valuation calibration, including precision and changing broker symbol
  specifications; current valuation is a declared linear/FX model.
- The complete monitor loop, initial tick and `time_msc` deduplication, forced
  post-fill checks and Dubai provisional entry protection.
- Gold555 first/additional-leg provisional and fill-adjusted SL/TP, trailing
  and their durable queue semantics.
- Exact pending-action worker scheduling, preparation, authorization, retries
  and restart persistence. Current retries use monitor cadence plus the existing
  declared protection cooldown, not a claim of worker parity.
- Full account-wide native read payloads: the current broker access exposes
  each selected basket's book. Shared transport contention is represented, but
  this is not an account-wide MT5 snapshot or a claim about unrelated positions.
- Multiple complete historical windows for both channels, accounting and
  exposure/floating/MAE/MFE/drawdown comparisons, with missing cases retained.
- Untouched validation, strategy-search admission, operational VM verification
  and separately authorized safe publication.

No commit, push, deployment, VM restart or live order was performed for this
milestone. The full goal remains active; completing this component is not
equivalent to a reliable end-to-end simulator.

## Next Integration Boundary

The next protection block should reuse canonical post-fill Gold555 queueing and
the real pending-action semantics, not duplicate merge/revision/retry rules in
`ClientBook`. Relevant existing evidence is in
`tests/test_management_decision_evidence.py`, `tests/test_e4_pending_revision.py`
and `tests/test_e4_pending_spool.py`. The latter two already exercise durable
execution/transport and atomic persistence around the protection queue.

Those fixtures are not a complete replay backend: the existing transport double
has fixed positions and real delays. The integration must connect to one
authoritative broker state and explicitly choose a single scheduling owner;
the runtime scheduler and `TransportSession` must not both grant the same call.
Do not call a second disconnected book an end-to-end simulation.

Required cases include SL-only then TP-only coalescing, TP prerequisites while
SL waits for market, a newer revision arriving during an older response, spool
failure and uncertain execution without blind resend. Running only `_try_once`
does not certify the queue's full `_run` loop. Initial/provisional protection,
full monitor order and later historical comparison remain part of the goal.
