# Gold Signals NOW Live Candidate

**Goal:** run the frozen Gold Signals NOW management candidate on the demo
account without changing zone execution or weakening broker-side protection.

## Frozen trading contract

- Scope: `canal2` formal `telegram_now` entries only.
- Entry: retain the current five immediate `0.01` market legs.
- Targets and provider management: observe and journal, do not execute.
- Basket stop: close at `-100 EUR` total realized plus floating P/L.
- Profit protection: arm at `+10 EUR`; close after an `8 EUR` giveback.
- Price break-even: each leg moves its broker SL to its own entry after a
  favorable `12 USD` XAUUSD move.
- Time exit: after 40 minutes, close only if total basket P/L is non-positive.
- Research fingerprint:
  `c4900550abae98de1500bf5b849072956175fdecda102fad69be9f7975cbf8d6`.

## Broker protection

Every leg opens with a real provisional SL. After the real fill, the bot
recalculates a per-leg catastrophe SL whose combined budget is no greater than
the frozen `100 EUR` basket stop. SL changes use the durable pending-action
queue and keep retrying while the signal is open. Failure to install an SL
raises one actionable alert but does not close the position solely because of
the installation failure.

## Safety and recovery

- Refuse activation outside a verified EUR demo account.
- Revalidate the active account immediately before opening each NOW basket.
- Start lifecycle supervision immediately after the first confirmed fill.
- Mark every candidate position in the MT5 comment for crash-safe recovery.
- Rebuild the frozen strategy after restart and reassert missing SLs even if
  the entry toggle has since been disabled.
- Keep provider levels, management and retractions as evidence only.
- Retry broker SLs every five seconds and guard-triggered closes until MT5
  confirms them or the signal has no exposure left.
- Preserve Gold zone behavior unchanged.
- Publish the exact policy and fingerprint in the startup contract.

## Verification

1. Unit-test the frozen fingerprint and guard transitions.
2. Unit-test BUY/SELL broker-stop calculation and persistent retry requests.
3. Test NOW-only routing, provider-level suppression and provider-action
   suppression.
4. Test MT5 comment recovery when the first leg has already closed.
5. Run the complete test suite.
6. Deploy only when MT5 reports zero open bot positions.
