# Gold 555 Demo Trial Design

## Goal

Run the frozen Gold Signals NOW candidate
`555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0`
for new Canal 2 `telegram_now` signals during a two-day demo trial. Keep Gold
zone handling unchanged, preserve recovery for existing `c490` baskets, and
record enough evidence to compare live execution with the exact tick replay.

## Considered approaches

1. **Reversible policy selector (selected).** Add `c490` and `555` as distinct
   policy identities and route only new NOW signals through the configured
   policy. Recovery follows the marker stored in each MT5 position. This adds
   a small amount of routing code but preserves attribution and rollback.
2. **Replace c490 in place.** Reuse the current strategy ID and overwrite its
   parameters. This is smaller, but restart recovery and historical evidence
   could silently confuse two different policies, so it is rejected.
3. **Shadow-only 555.** Keep c490 trading and calculate 555 decisions without
   orders. This is safest, but it does not satisfy the requested live demo
   comparison, so it is rejected for this trial.

## Frozen 555 contract

- Scope: Canal 2 formal `telegram_now` BUY/SELL only.
- Account gate: verified MT5 demo account denominated in EUR.
- Entry window: 30 minutes from the original Telegram observation time.
- Initial entry: wait for a 1.00 USD adverse move from the first usable quote,
  then for a 1.50 USD reversal from the running adverse extreme.
- First leg: 0.04 lots at the confirmed reversal.
- Additional adverse legs from the first real fill: 0.03 lots at 1.50, 3.00,
  4.50 and 6.00 USD adverse distance, while the original entry window remains
  open. Maximum planned signal volume is 0.16 lots.
- Per-leg targets from each real fill: 0.50, 1.00, 1.50, 2.00 and 2.50 USD in
  favourable direction.
- Break-even: disabled, including provider BE instructions.
- Stop: a real broker-side 30.00 USD trailing stop per leg, installed from the
  real fill and tightened on every new broker tick. The stop may never loosen.
- Basket profit lock: arm when realized plus floating P/L reaches 30 EUR and
  close all remaining exposure after a 1 EUR giveback from the peak.
- Time exit: from 180 minutes after the first fill, close when total basket P/L
  is non-negative. Negative baskets are not closed solely by this rule.
- Provider management: execute explicit supported close instructions; ignore
  provider TP, SL and BE level changes because 555 owns those rules.
- Hard horizon: preserve the research contract, which has no separate forced
  time exit. Log and alert prolonged exposure rather than inventing a result.

## Runtime design

`GOLD_NOW_LIVE_POLICY` selects `c490`, `555`, or `legacy`; the default remains
the currently deployed `c490` until the VM explicitly selects `555`. Every
basket stores its immutable policy ID and fingerprint in state, journal events
and MT5 comments.

A dedicated pre-entry watcher owns a received 555 intent before any order is
opened. It consumes each new MT5 tick, persists its reference, adverse extreme,
trigger state and expiry, and claims the intent exactly once. On confirmation
it opens the first leg, creates normal signal state and starts lifecycle
supervision. Expiry produces an explicit unfilled record and no order.

After the first fill, the existing every-new-tick lifecycle monitor opens
sequential adverse legs, installs each leg's fixed TP and real trailing SL,
updates trailing stops, evaluates the basket profit lock and conditional time
exit, and retries failed MT5 actions through the durable pending queue.

Startup recovery identifies both c490 and 555 comments independently. Existing
c490 exposure keeps c490 management even when the selector is 555. A recovered
555 basket reconstructs entries, filled leg indexes, target ranks, current
stops, expiry and basket guard state before supervision resumes.

## Failure handling

- No 555 order may be sent when the account gate, symbol contract, tick,
  volume alignment or strategy fingerprint cannot be verified.
- After any confirmed fill, optional telemetry failures cannot prevent state
  registration or protection.
- An SL or TP installation failure remains in the durable retry queue and
  raises one actionable alert.
- The pre-entry watcher cannot open twice after duplicate Telegram delivery or
  restart.
- A policy mismatch fails closed for new entries but never abandons already
  open exposure.

## Evidence and acceptance

Tests must cover BUY and SELL entry state machines, expiry, moving extremes,
sequential ladders, real-fill targets, monotonic trailing stops, provider close
routing, duplicate prevention, restart recovery and policy isolation.

Deployment is allowed only with the full test suite passing, a clean branch,
zero open MT5 positions, a verified demo EUR account and a startup contract
showing `gold_now_555_v1`. The VM must then record the selected fingerprint and
the pre-entry watcher state before the first eligible signal.
