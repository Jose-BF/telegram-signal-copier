# Provider-Centric Exact Simulation Design

Date: 2026-07-14
Status: approved for implementation

## Objective

Build the simulation source of truth from the Telegram provider timeline and
broker ticks, rather than from whatever the live bot happened to execute.
Every formal provider signal must appear in every farm run. A signal may carry
an explicit policy-specific blocker while evidence is incomplete, but it may
never disappear silently.

The engine must keep three claims separate:

1. price-path replay: the causal bid/ask path and level touches are known;
2. monetary replay: the result can be calculated in account currency;
3. broker-exact replay: the monetary formula has reproduced observed MT5 deals
   to the account currency precision.

Only the third claim may be used for final policy ranking in money.

## Why The Current Farm Is Not Enough

The current farm starts from `data/replay_trades.jsonl`. That means it can only
simulate signals which produced an MT5 execution record. The canonical provider
catalog is attached afterwards, so unexecuted provider signals are counted but
not simulated.

The current monetary model also infers a price-unit value from final MT5 P&L.
That reproduces the observed close by construction, but it cannot guarantee the
same account-currency result for a counterfactual close at another timestamp.
The conversion rate may have changed.

## Architecture

### 1. Canonical Provider Entry Contract

`provider_signal_catalog.py` will publish an `entry_contract` for every formal
signal:

- `trigger_observed_utc`: first locally observed actionable provider event;
- `trigger_telegram_utc`: provider timestamp for diagnostics only;
- `trigger_message_id` and `trigger_kind` (`sticker`, `text`, or `edit`);
- direction and source of that direction;
- ordered causal revisions and management events;
- links to zero, one, or several observed execution signal IDs.

The locally observed timestamp is the causal boundary. Telegram server time is
useful for measuring delivery delay, but the simulator must not trade before
the message was available to the bot.

Canal 1 sticker plus following text is one provider signal. The sticker defines
the entry trigger; the text supplies levels when it is observed. Closing the
sticker-triggered live position before the text arrives must not turn the text
into a second entry unless the provider explicitly requests re-entry.

Canal 2 uses the first revision that contains an actionable direction. Later
edits update range, TP and SL causally; they cannot be back-applied to earlier
ticks.

### 2. Virtual Trade Construction

A new provider-first builder will create one virtual trade specification per
formal provider signal. It will not require an MT5 ticket.

The default entry model is market execution at the first eligible tick at or
after `trigger_observed_utc + latency_ms`:

- BUY opens at Ask;
- SELL opens at Bid;
- no signal is rejected merely because the observed price is outside the
  provider range;
- all assumptions, including latency and slippage, are stored in the run card.

Farm runs will evaluate deterministic latency scenarios rather than hide entry
uncertainty. Initial scenarios are zero execution delay, observed p50 delay and
observed p95 delay. Policy selection eventually requires a result robust across
the approved latency scenarios.

Each policy declares the evidence it needs. A policy which uses provider TPs
may be blocked for a direction-only sticker, while a time-based or price-based
policy can still simulate that same signal. Incomplete evidence therefore
blocks a policy/signal pair, not the complete farm.

### 3. Broker Monetary Contract

At MT5 connection time the journal will capture immutable account and symbol
evidence:

- account currency and currency digits;
- symbol name, calculation mode, base/profit/margin currencies;
- contract size, point, tick size and profit/loss tick values;
- volume limits and step;
- swap mode and daily swap multipliers;
- resolved direct or inverse conversion symbol when profit currency differs
  from account currency.

The tick cache will retain verified bid/ask ticks for both XAUUSD and the
required currency-conversion symbol. Every file receives the UTC-v3 semantic
contract, hash and anchor validation already required for XAUUSD.

For CFD/Forex-style XAUUSD contracts, gross profit in profit currency is:

`directional_price_change * contract_size * volume`

It is then converted using the contemporaneous direct or inverse conversion
quote and rounded to the account currency digits. Positive and negative values
use the broker-appropriate Bid/Ask side. The implementation will not trust that
choice until it reproduces observed MT5 deal profit to the cent on the retained
corpus.

Commission, fee and swap are separate components. A run is broker-exact only
when each applicable component has verified evidence. Intraday zero-commission,
zero-swap trades can be exact after corpus validation. Trades crossing rollover
remain blocked until swap calculation is validated.

Legacy `ticket_mt5_calibrated` and global unit-value calculations remain
diagnostic only and can never enter final monetary rankings.

### 4. Validation Against Observed Executions

Observed MT5 tickets are validation evidence, not the source of virtual trades.
For signals the bot executed, the validator will compare:

- canonical trigger and virtual entry tick;
- actual fill latency and slippage;
- first-touch close time, side, reason and price;
- gross profit, conversion, commission, swap, fee and final net P&L.

The system will publish mismatch distributions rather than forcing a false
exact label. Broker-exact promotion requires every eligible validation ticket
to match at account-currency precision and no causal look-ahead.

### 5. Recursive Reliability Closure

The existing pattern learner remains offline. This work will promote patterns
only after code, a deterministic fixture, a permanent regression test, a full
retained-corpus shadow pass and explicit review metadata exist.

The first intended promotions are:

- duplicate Canal 1 execution caused by sticker/text pairing;
- repeated structurally impossible MT5 stop modification;
- benign recurring provider announcements currently counted as unknown;
- actionable exit/risk guidance currently left unclassified.

Historical incidents remain visible after a rule is covered. Only incidents
after `covered_after_utc` constitute a regression.

## Strategy Expansion Boundary

The current 22 policies only allocate legs between close-now, break-even and
runner at a BE management trigger. They remain a smoke-test catalog.

After provider-first and monetary validation pass, policy generation can add:

- floating-profit capture thresholds;
- giveback from MFE;
- fixed and adaptive time exits;
- numeric and trailing stop rules;
- partial-close schedules;
- TP allocation and runner rules;
- lot/risk allocation;
- combinations evaluated under fixed IS/OOS partitions.

No policy may be selected before an untouched OOS period is registered. The
large policy search begins only after the replay engine is validated, to avoid
optimizing against simulation errors.

## Failure Rules

- No formal provider signal may be omitted from a run.
- Missing ticks, conversion data, contract evidence or causal levels produce a
  named blocker, never an inferred exact value.
- A diagnostic-only result cannot appear in policy ranking.
- A stale farm, catalog or tick contract cannot be combined with newer inputs.
- Offline simulation modules must never be imported by live order modules.
- No code is pushed while the production bot is in an active trading session.

## Verification

Required automated coverage:

- Canal 1 sticker/text produces exactly one canonical entry, even if the live
  position closed before the text arrived;
- Canal 2 edits activate only from their observed timestamp;
- unexecuted signals produce virtual trade rows;
- BUY/SELL use Ask/Bid correctly at entry and exit;
- direct and inverse currency conversion use the correct quote side;
- missing conversion ticks block money but preserve price-path results;
- observed MT5 deals reconcile to account-currency precision;
- every formal signal appears once per policy and latency scenario;
- run fingerprints change for any input, contract or policy change;
- full `python -m pytest -q` passes;
- whole retained corpus shadow report has no silent signal loss.

## Delivery Sequence

1. Publish and test canonical entry contracts.
2. Build provider-first virtual trade specifications and price-path replay.
3. Capture broker/account/symbol evidence in live logs.
4. Add conversion-symbol tick contracts and exact monetary validation.
5. Move the farm from executed-trade iteration to provider-signal iteration.
6. Fix live sticker/text duplicate entry and promote reviewed patterns.
7. Regenerate UTC-v3 artifacts on the VM and run the complete shadow corpus.
8. Freeze an OOS window before expanding the policy catalog.

## Acceptance Criteria

The foundation is complete when:

- every retained formal provider signal has one row for every tested policy;
- unexecuted signals are simulated from causal Telegram observation and ticks;
- executed eligible trades reproduce MT5 gross and net P&L to the cent;
- every non-exact row names the missing evidence;
- no ranking is published from diagnostic or estimated money;
- repeated identical inputs produce the same immutable run fingerprint and
  byte-identical result.

## Official MT5 Basis

- Order profit is calculated in account currency under the current market
  environment: https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcprofit_py
- CFD/Forex profit uses price change, contract size and lots before currency
  conversion: https://www.mql5.com/en/book/automation/experts/experts_ordercalcprofit
- Symbol contract and tick-value properties are exposed by MT5:
  https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants
- Historical ticks must be requested with UTC datetimes:
  https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py
