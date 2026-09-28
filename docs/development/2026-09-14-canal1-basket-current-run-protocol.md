# Canal 1: fixed comparison run after basket support

Frozen before economics. Local research only; no publication, live settings,
MT5 connection, orders, promotion, account capital or Canal 2.

## Matrix and clocks

Reuse all 258 source identities from signal_anatomy_v2. Dubai uses the initial
causal receipt and the local dubai_balanced_v1 contract: 0.01/0.04/0.04 lots,
adverse adds at 4/8 XAU from first scenario fill, 15-minute add expiry,
25 EUR basket stop, profit lock 10/2 EUR, loss-only exit after 40 minutes.
Loss-only means total basket P/L <= 0. Basket decisions request market closure;
processing latency can overshoot the nominal threshold. No forced 60-minute exit.

Range controls use first priced range, absolute SL/TP1, one immediate order,
60-minute exit, no BE. Recompute native 200 USD nominal sizing and a 25 EUR
nominal alternative. Equal sizing uses request quote and causal EURUSD Bid
for conversion of a loss, rounds DOWN to 0.01 lot, caps at 1 lot and abstains
below minimum. R05 is the unchanged request-time RR >= 0.50 overlay. Never
multiply historical P/L to simulate smaller positions. No parameter search.

Three base policies x two execution profiles x 258 signals. R05 native and
equal-risk overlays add no engine evaluations. Include unavailable cases.
Compare own known universes and common-known identities. The old twelve R05
fills are a secondary retrospective diagnostic, never a Dubai entry filter.

## Coverage and management

Keep previous strict Bid/Ask, FX, gap, broker-clock and intraday coverage gates.
Range horizon remains 3910 seconds. Dubai first tries 14400 seconds. When that
data window fails coverage, try 3910 seconds with identical gates; this fallback
is decided on source coverage, not trading outcome. Only naturally completed
baskets or known no-orders count as known. Still-open/pending/blocked outcomes
remain unknown, not zero. Report full-horizon and fallback cohorts separately.
This bounded window is not proof of outcomes beyond four hours.

For Dubai retain only associated, barrier-free management messages with direct
modality and a strategy-close action under the canonical exact vocabulary.
Use receipt time, including causally received edits; deduplicate exact source
rows. Optional/conditional/informational messages do not execute. Partial close
means whole basket under this contract. Retain source message and inferred
association evidence. This deterministic interpretation is NOT proof of the
historical runtime interpreter or today's VM state.

## Execution and evidence

Same two prior latency/slippage/spread/volume profiles. Range uses the existing
absolute_levels_be_v1 profile; Dubai explicitly opts into basket_guard_v1.
Historical spread and FX plus 10 EUR per filled lot round-trip. No overnight
swap estimate, margin, concurrent portfolio, deposits or guaranteed loss cap.
All calculable base rows run scalar, fast and independent oracle. Any
disagreement stops the run with an immutable incident. Native range lifecycle
must equal all prior no-BE controls except implementation behavior digest.
Preserve old source versions with sources_before_basket_guard_v1; do not edit
old runs. Bind new sources, policies, inputs and results with hashes and verify.

Limits: 300 source signals, 20000 evaluations, 150 million decoded source
quotes, 12 billion engine quote visits and four hours wall time. No automatic
repeat. End with economic results, coverage/status counts, monthly and daily
concentration, paired comparisons and evidence limitations. Reused discovery
is not untouched OOS; no automatic winning candidate or live certification.
