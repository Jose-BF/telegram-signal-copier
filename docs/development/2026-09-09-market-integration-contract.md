# Market Integration Contract

Local opt-in hypothesis, preserving every legacy result when execution.market
is None. Parent owns market_contract.py, scalar market.py, engine.py, shared
tests and final verification. Independent ports own only oracle.py or
fast_engine.py and their focused tests. No live/runtime imports or deployment.

## Public Contract

- ExecutionAssumptions and ExecutionScenario gain market: MarketProfile | None.
- EntryRecord and OracleEntry gain acknowledged_ns: int | None = None. Populate
  it only when an acknowledgement is actually processed inside the replay.
- SimulationResult and OracleResult gain market_events: tuple[MarketEvent, ...].
- MarketEvent kinds: entry_requested, entry_filled, entry_rejected,
  entry_acknowledged, close_requested, close_filled, close_rejected,
  close_acknowledged. Request IDs are positive, assigned in request order across
  open and close requests. Event price is request quote, fill price or None for
  rejected requests; ack repeats its outcome price. Reasons: entry policy source
  on request/fill; invalid_volume or invalid_initial_protection on rejection;
  accepted or rejection reason on entry ack; strategy reason on close request/
  fill; position_already_closed on close rejection; accepted/rejection on ack.

## Scope And Event Ordering

MarketProfile requires a ProtectionProfile and schema 2 hypothetical entries.
Actual MT5 fills lack entry acknowledgement evidence and are blocked in this
new mode, rather than shifted. Existing market=None controls remain available.
Existing protection policy/freeze restrictions are preserved initially.

1. At their scheduled request quote, record entry requests only if the strategy
   has not cancelled the remaining plan. A request is not an entry.
2. On processing/fill quote, validate volume min/max/step and requested initial
   protection using that quote. Rejection records no fill/protection and cancels
   later unrequested legs; existing positions remain managed after the ack.
   No automatic entry retry is claimed. Preserve prior completed exits.
3. Execute already-installed passive SL/TP first on each quote. Then process due
   market closes, then due protection modifications/acks. Close processing must
   have index > request index and reach its configured delay. A passive exit
   before a requested close makes that close reject; never double-realize P/L.
4. Entry ack is on first usable quote at/after fill/reject + configured delay.
   Record it even if a passive stop closed the position meanwhile. No management
   decision while any entry ack is outstanding (declared serialized client).
   Ladder request scanning starts on the next quote after the previous entry
   acknowledgement, including zero-delay profiles. This declared quote-clock
   serialization prevents later legs from being dispatched before a rejection.
5. Strategy decisions can request full-position market closes for hard stop,
   profit lock, provider close and time exit under the supported profile. A
   pending close does not remove the position. No new modify for that ticket;
   prior modification requests may still complete while it remains open.
6. A basket-close intent latches, cancels future unrequested entries and closes
   known positions. An entry already in flight at that intent is explicitly
   blocked as market_close_with_entry_in_flight_unsupported (not silently
   cancelled or filled). This limitation must remain visible to admission.
7. Close acknowledgements arrive after close processing + configured delay.
   Keep replay alive for pending market results/acks after becoming flat. If
   data end first, preserve completed prefix and block incomplete lifecycle.
   A closed position's late modify responses are not certified by this model.

No automatic close retry is needed for the modeled position-already-closed
rejection. Other broker close rejects, margin/stop-out, partial broker fills,
deviation/requotes and concurrent client scheduling remain unmodeled, not
calibrated by the profile. Fixed profiles are controls, not trading candidates.

Budget exhaustion blocks with retained trace and no invented exits. Missing
future acknowledgements must not erase earlier fills or passive exits. A
market-only change invalidates prior implementation-bound prospective freezes.
