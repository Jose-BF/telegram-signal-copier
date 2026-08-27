# telegram-signal-copier

Telegram signal copier for MetaTrader 5.

## Project Map

Production runtime:

- `run_bot.bat` starts the Windows watcher loop.
- `tools/run_bot_watch.py` keeps code updated, restarts the bot and launches telemetry publication without waiting for the network.
- `runtime_data/` contains live evidence and console diagnostics. It is ignored by Git; production never writes into tracked `data/` files.
- `main.py` starts Telegram, MT5, reconciliation/resync and runtime monitors.
- `listener.py` interprets Telegram messages and routes signal/management actions.
- `executor.py` sends and modifies MT5 orders.
- `position_lifecycle_monitor.py` watches open signal lifecycle: BE, time-stop, auto-finalize and leftover position handling. This was formerly the DCA monitor; do not use the old name for new work.
- `state.py`, `journal.py`, `pending_actions.py`, `live_auditor.py`, `strategies.py`, `parser.py`, `classifier.py` are runtime support modules.

Gold Signals zone execution:

- Immediate `BUY/SELL NOW` messages keep their established execution path.
- A formal plan with one BUY/SELL zone, at least one TP and an SL is armed on
  the demo account. BUY uses broker Ask and SELL uses broker Bid. Its first
  fresh touch is recorded for analysis but does not open exposure.
- Explicit `Active` or `You can enter` may open immediately. Explicit
  re-entry creates a new generation; `Do not re-enter` blocks later ones.
- Multi-zone maps and incomplete plans remain observation-only until one
  complete, unambiguous plan exists. Old pre-schema zone observations are
  never promoted to live execution after an upgrade.
- Reply aliases, revised TP/SL values, trigger tick, plan identity and entry
  generation survive restart and are exposed as `entry_provenance` in ledger
  and replay records.
- `Close overall profit OR set breakeven` is one contextual action: a positive
  live basket closes, while a zero/negative basket receives exact per-ticket
  breakeven. Both branches are never executed together.

Current demo forward policy:

- Dubai Investing runs the frozen `dubai_balanced_v1` demo candidate. It opens
  `0.01` lots immediately, then `0.04` after an adverse XAUUSD move of `$4`
  and another `0.04` after `$8`; the two delayed legs expire after 15 minutes.
  It installs no per-ticket TP, SL or BE. The aggregate EUR basket closes at
  `-25`, arms a dynamic lock at `+10`, closes after a `2` EUR giveback, and
  closes at 40 minutes only when its total P/L is not positive. Explicit
  provider closes are honoured; other provider management remains evidence.
- Gold Signals explicit `BUY/SELL NOW` messages run the frozen
  `gold_now_c490_v1` demo candidate. It opens five immediate `0.01` positions,
  installs no TP and does not execute provider management. The aggregate EUR
  basket closes at `-100`, arms profit protection at `+10`, closes after an
  `8` EUR giveback, and closes at 40 minutes only when total P/L is not
  positive. Each leg moves its own broker SL to its exact fill after a
  favorable `$12` XAUUSD move.
- Every Gold NOW leg opens with a real provisional broker SL. The bot then
  recalculates it from the actual MT5 fill with a `20 EUR` per-leg loss budget,
  verifies all five SLs every five seconds and retries persistently while the
  signal remains open. A failed install raises one actionable alert but does
  not close the basket solely because of that failure.
- The active MT5 account is revalidated as EUR demo immediately before each
  Gold NOW basket. Supervision starts as soon as the first ticket is known,
  guard closes remain queued until MT5 confirms them, and `_gv1` positions
  recover the same policy after restart even when new candidate entries have
  been disabled.
- Gold zone plans remain independent: they open only after an explicit
  provider activation; first touch remains observation-only.
- Dubai candidate exposure is frozen at `0.09` lots per signal. Gold c490 and
  legacy retain a `0.05`-lot maximum. The separately identified Gold 555 demo
  trial can reach `0.16` lots and therefore requires the explicit matching
  `GOLD_555_MAX_PLANNED_LOTS_PER_SIGNAL=0.16` gate; that permission cannot
  increase the cap used by c490, zones, legacy scale-out or rescue entries.
- Startup publishes a `live_strategy_contract` event and one readable console
  line containing the candidate ID, exact fingerprint and thresholds. Startup
  refuses the candidate on a real, unverifiable or non-EUR MT5 account.
- These are forward demo trials calibrated on retained history, not evidence
  of guaranteed profitability. Set `STRATEGY_C1_BALANCED_V1_ENABLED=0` for the
  previous Dubai path, or select `GOLD_NOW_LIVE_POLICY=c490`, `555` or `legacy`
  for new Gold NOW baskets. Existing baskets retain their recorded policy.
- The Gold 555 selector is an in-sample candidate under prospective forward
  testing, not an independently validated winner. It logs every entry-watch
  transition and broker-tick decision, and emits one alert if a negative
  basket remains open after its three-hour non-negative exit threshold.
- Both candidate basket guards sample every fresh broker tick. Dubai remains
  process-protected; Gold NOW also has a broker-side catastrophe SL on every
  leg so a stopped Python process does not leave those positions naked.

Prospective strategy shadows:

- `STRATEGY_SHADOW_ENABLED=1` observes every accepted Dubai signal and every
  Gold `BUY/SELL NOW` signal with three frozen candidates per channel. Gold
  zone plans are deliberately excluded from this first cohort.
- Shadows consume normalized Telegram events and fresh broker ticks, but their
  modules cannot import the MT5 order path. They never open, modify or close a
  real position and normal differences do not generate human-review alerts.
- Transition-only checkpoints preserve causal recovery across a restart.
  Missing ticks, Telegram lineage, verified EUR conversion, prospective
  registration or live-control parity block the ranking instead of estimating
  the missing result.
- Entry windows start when Telegram is accepted; holding-time exits start at
  the first virtual fill. An unexpected shadow-runtime failure disables only
  observation and leaves the live bot running.
- Reviews are labelled diagnostic at 15 untouched signals per channel,
  provisional at 45 and evidence at 100. A ranking never promotes or changes
  the live policy automatically; candidate parameters remain frozen throughout
  their forward cohort.
- The rollback is immediate and isolated: set
  `STRATEGY_SHADOW_ENABLED=false`. This changes only observation and leaves the
  active Dubai/Gold execution policies untouched.

The compact daily log pass is incremental and reports armed zones, confirmed
entries, trigger types and failures without rescanning the retained corpus:

```powershell
python tools\analyze_new_logs.py
```

Replay and simulation foundation:

- `reconcile_mt5_ledger.py` rebuilds `runtime_data/ledger.jsonl` from bot logs plus MT5 history.
- `build_replay_trades.py` builds `runtime_data/replay_trades.jsonl` from
  ledger and event history and writes
  `runtime_data/replay_trades.jsonl.manifest.json`.
- `replay_source_contract.py` binds that replay to the exact SHA-256 and size
  of its MT5 ledger and raw event stream. The farm rejects missing, changed or
  stale source evidence before calculating policies.
- `accounting_replay_validator.py` validates reconstructed trade accounting into `runtime_data/accounting_replay_audit.jsonl`.
- `tools/ensure_replay_tick_cache.py` ensures MT5 tick parquet files exist and verifies the `mt5_server_epoch_utc_v3` time/anchor contract and SHA-256 for every cached day.
- `replay_readiness_report.py` reports whether each trade has enough data for full replay.
- `observed_tick_replay_validator.py` checks whether cached bid/ask ticks reproduce the observed MT5 ticket closures.
- `mt5_tick_cache.py` is the local parquet tick-cache helper.
- `provider_signal_catalog.py` groups raw Telegram messages and edits into one canonical provider signal, including signals the bot did not execute.
- `provider_trade_spec.py` turns every formal provider signal into an immutable virtual-trade contract without requiring an MT5 ticket.
- `provider_strategy_simulator.py` enters BUY at Ask or SELL at Bid after the configured causal latency, then replays policy price paths over verified ticks.
- `provider_zone_spec.py` reconstructs each Gold Signals zone only from values
  already observed at that instant; later edits cannot leak backwards.
- `zone_strategy_farm.py` compares first-touch, provider-`Active` and layered
  zone entries at equal planned risk. It publishes daily and per-layer P&L,
  retains every incomplete or tick-blocked plan in the denominator and audits
  zone penetration with an independent implementation.
- `strategy_policies.py` defines the shared close/BE/runner policy matrix for both channels.
- `strategy_simulator.py` replays alternative management over the entries,
  volumes and confirmed level history actually executed by MT5. Canonical
  Telegram events supply management-trigger times, not replacement entries.
- `executed_simulation_contract.py` fails closed if any policy omits a trade,
  duplicates a row or changes an observed MT5 ticket, fill time, fill price or
  volume.
- `strategy_farm.py` ranks only the executed-MT5 universe. It also evaluates
  formal Telegram signals, including unexecuted signals, as a separate
  provider-coverage diagnostic which is never ranking-eligible.
- `simulation_run_provenance.py` fingerprints the exact selected farm inputs, policy order, source files, runtime versions and tick contracts already verified by the replay loader.
- `runtime_data/simulation_runs/<fingerprint>/run_card.json` is immutable run evidence. Repeating identical computational inputs reuses the same directory; a contradictory result fails closed.
- `mt5_tester_replay.py` exports a frozen executed-MT5 ticket universe to a
  tester-only MQL5 EA. Its `observed_close` baseline must reproduce the
  official account history to the cent before TP2 alternatives are reported.

Offline iterative Dubai research:

- `research/dubai_iterative/` explores causal combinations of entry timing,
  simultaneous or adverse/favourable entry ladders, broker-valid lot
  allocations, provider or independent exits, BE, stops, profit protection,
  time exits, provider management and observable context filters.
- `0.04` lots is the observed comparison baseline, not a search ceiling. Each
  run records an explicit `--min-total-volume`, `--max-total-volume`,
  `--max-legs` and broker `--volume-step` envelope in its immutable run card.
- Rule quality is ranked per `0.01` planned lot before raw exposure, so merely
  multiplying an otherwise identical bet cannot become a better strategy.
- The fast fixed-point engine must agree to the cent with an independent
  scalar oracle. Finalists are also replayed under latency, slippage and wider
  spread stress. Any mismatch, missing signal or incomplete money evidence is
  visible and blocks a reliability claim.
- Results under `runtime_data/dubai_strategy_runs/` are research artifacts.
  The package cannot import live execution modules, change runtime settings,
  restart the bot or deploy a candidate.

Example bounded run over the current robust retrospective window:

```powershell
python -m research.dubai_iterative `
  --from 2026-07-27 --to 2026-08-14 `
  --max-total-volume 1.00 --max-legs 12 `
  --max-generations 8 --population-size 64 `
  --max-evaluations 800 --max-wall-seconds 1800 `
  --oracle-finalists 3 --progress
```

The search is iterative but never recursive: generations, evaluations, wall
time, stale generations and lineage depth are all hard stopping conditions.
A retrospective result remains unvalidated until it survives untouched
forward/OOS evidence with the project sample-size and significance gates.

Current frozen Dubai research checkpoint (2026-08-22):

- The retrospective universe is `2026-07-27..2026-08-14`: 45 exact signals
  across 15 sessions. The observed bot result was `-219.00 EUR`.
- The search screened 100,000 broad full-rule candidates, 100,000 management
  candidates and two local refinement passes. In total it executed more than
  ten million signal replays rather than ranking a small hand-picked list.
- `dubai_balanced_v1` produced `+269.99 EUR` retrospectively, with `47.12 EUR`
  maximum drawdown and positive results in each of the three calendar weeks.
  The scalar oracle agreed to the cent and the rule remained positive in six
  predefined latency, slippage and spread worlds; the weakest world returned
  `+161.52 EUR` with `69.88 EUR` maximum drawdown.
- Those figures selected a hypothesis on already-seen data; they are not an
  untouched validation set. Fingerprint
  `32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631`
  is frozen for the demo forward trial. Results are reviewed after 15, 45 and
  100 new signals without changing the rule between checkpoints.

`runtime_data/provider_signal_catalog.json` is a canonical, versioned farm input. It
must travel with `runtime_data/strategy_farm.json` and the corresponding run card; it
is not a disposable intermediate report.

Run the current clean-window diagnostics manually:

```powershell
python reconcile_mt5_ledger.py --quiet
python build_replay_trades.py --quiet
python provider_signal_catalog.py
python strategy_farm.py --from 2026-07-06
python strategy_farm.py --from 2026-07-06 --provider-latency-ms 0 --provider-latency-ms 150 --provider-latency-ms 250
python strategy_farm.py --from 2026-07-06 --include-trades --output runtime_data/strategy_farm_detail.json
```

Run the offline Gold Signals zone experiment separately from the live bot:

```powershell
python zone_strategy_farm.py `
  --catalog runtime_data/provider_signal_catalog.json `
  --tick-cache runtime_data/ticks_cache `
  --money-contract runtime_data/broker_money_contract.json `
  --money-tick-cache runtime_data/money_ticks_cache `
  --observed-replay runtime_data/replay_trades.jsonl `
  --since 2026-07-29 --until 2026-08-05 `
  --output runtime_data/zone_strategy_farm.json
```

This farm is research-only: it cannot import live execution modules or change
orders. `observed_execution_summary` proves actual MT5 fills against ticks;
`modeled_baseline_summary` separately measures how closely the deterministic
zero-latency policy resembles those fills. A mismatch in the second does not
erase valid observed execution evidence. Comparisons use both raw and
risk-normalized P&L, while incomplete plans and invalid tick days remain
visible blockers. Research runs never invent a close at the end of the cached
day: a position that is still open is reported as `open_at_horizon`. Explicit
re-entry and re-arm-after-terminal lifecycles remain named blockers until each
generation can be simulated independently. No policy is eligible for live
promotion without untouched forward/OOS evidence.

Prepare an independent Strategy Tester proof after rebuilding
`runtime_data/replay_trades.jsonl`:

```powershell
python mt5_tester_replay.py prepare --date 2026-07-27
python mt5_tester_replay.py certify --run-dir runtime_data\mt5_tester_runs\2026-07-27
```

When a policy can remain open after midnight, declare the exclusive tester
horizon instead of treating the calendar boundary as a close:

```powershell
python mt5_tester_replay.py prepare --date 2026-07-21 --tester-until 2026-07-24
```

TP2 policies then keep positions open across real ticks until TP2, provider
SL, or the declared horizon. Anything still open at that horizon is blocked
and excluded from totals. A fixed end-of-day close is a different policy; the
tester boundary never invents it.

Alternative positions that cross broker rollover are priced only when the
versioned money contract contains matching native MT5 evidence around each
non-zero rollover. Weekend zero-multiplier boundaries may use matching
pre-close and post-open specifications; changed or missing evidence blocks the
result. The simulator never presents P&L that silently omits swap.

Install the read-only native evidence service once per MT5 terminal:

```powershell
python tools\install_broker_money_snapshot_service.py
```

Then add `BrokerMoneySnapshotService` visibly from
`Navigator > Services > TelegramSignalCopier`, leaving algorithmic trading
permission disabled, and verify it:

```powershell
python tools\install_broker_money_snapshot_service.py --verify-only
```

Verification requires a fresh terminal-local XAUUSD snapshot, a certifiable
money contract and matching hashes for the reviewed source and compiled EX5.
It reports `INACTIVE` instead of accepting a stale or replaced binary.

The bot reports `Registro simulacion: activo` at startup. A later capture
failure or recovery is notified without pausing live trading. The service
writes one tiny atomic native snapshot per minute inside that terminal's own
data directory; another MT5 installation cannot overwrite or impersonate it.
Python retains only startup, contract-change and rollover evidence. Recovered
snapshots must match the anonymous account fingerprint, server, symbol and
journal payload hash before they can certify money.

`mt5_tester_replay.py prepare` reads the live ignored contract from
`runtime_data/broker_money_contract.json` by default. It never falls back to
the obsolete tracked fixture under `data/`.

Certification rechecks the exact fixture copies, compiled EA, INI and SET
profiles before accepting any result. All declared policies appear in the
summary: missing or malformed outputs are explicit blockers.

The generated profiles use XAUUSD M1, Model 4 real ticks, EUR account
currency and local agents only. `TelegramSignalReplayEA` refuses to run
outside the tester and never sends or modifies a real order. Alternative
policies remain diagnostic until the observed baseline is certified and an
untouched OOS sample is validated.

MT5 treats `ToDate` as the exclusive end of the test. A day can be prepared
while it is still open, using an explicit UTC cutoff, but its tester proof
must wait until the next calendar day. With terminal build 6061, start each
MCP tester policy from a fresh local terminal; reusing the same controller can
stop the second run early. This restart concerns only the local research
terminal, never the production VM or bot.

The watcher accepts the same assumptions through
`STRATEGY_FARM_LATENCY_MS=0,150,250` and
`STRATEGY_FARM_VOLUME_PER_LEG=0.01`. Scenario order and values are part of the
immutable run fingerprint. Use measured latency percentiles when available;
do not silently replace them between runs.

Use `--run-archive-dir <path>` to override the default
`runtime_data/simulation_runs` archive. Compact farm runs retain a copy of the report
inside their fingerprint directory. `--include-trades` reports can be large,
so the first-published output path, exact size and SHA-256 are recorded but the
report is not copied into the archive.

`--ensure` automatically replaces legacy, unversioned or tampered cache days.
`--refresh-day` remains available when a known day must be forced manually:

```powershell
python tools/ensure_replay_tick_cache.py --ensure --refresh-day 2026-07-08 --refresh-day 2026-07-09 --refresh-day 2026-07-10
```

`executed_scope.rows_expected` must equal executed trades multiplied by
policies, and `rows_emitted` must match it. Every usable row must preserve the
observed MT5 ticket set and entry facts. `provider_scope.rows_expected` must
also equal `rows_emitted`, and `signals_omitted` must be empty. Missing ticks
or causal levels create visible blocked rows; they never remove a trade or
signal from its denominator.

Primary `policies` and `selection` use only positions actually executed in MT5.
Provider-first `strategy_value` remains available under the secondary
diagnostic section; it is directional XAUUSD movement, not account-currency
P&L, and must not be interpreted as profitability.
MT5 gross/net P&L must reproduce to the cent with conversion, commission and
swap evidence before a run may be treated as verified. Baseline replay
requires verifiable bid/ask ticks at both the MT5 entry and exit, matching
first-touch time, reason and fill price. A fully observed external
intervention may remain strategy-eligible only in the executed-MT5 universe;
it is never labelled an exact causal replay. Blocked tick replay, stale replay
sources, small samples and an unvalidated untouched OOS period prevent
strategy selection.

`validation.money_contract_verified` describes the broker formula and
conversion contract. `validation.account_currency_money_verified` separately
requires every observed and counterfactual row in the selected run to be
priced successfully. Both must be true before conclusions are allowed.

Tick run-card digests prove which local Parquet bytes were used at execution
time. The Parquet cache itself is currently local-only: a run card cannot
recreate a cache file after that file has been deleted. Such cards therefore
report current verification separately from durable artifact retention.

### Causal execution evidence

New runtime events carry a stable chain from the Telegram message revision to
the bot decision, logical action, individual MT5 attempt and broker result.
This tracing is passive: it does not change order payloads, retry timing or
trading decisions.

Audit a downloaded runtime corpus with:

```powershell
python tools/audit_causal_lineage.py --events runtime_data/trade_events.jsonl --output runtime_data/causal_lineage_audit.json
```

`complete` means that the causal evidence chain is present. It does not mean
that the trade or strategy was profitable. Historical rows recorded before
this contract remain visible as `legacy_before_contract`; they are never
discarded. Any missing or contradictory link blocks exact counterfactual
certification for the affected evidence. The audit also verifies each event
envelope and payload hash, follows references outside an optional date filter,
and reports coalesced or superseded actions instead of treating them as
unexplained missing attempts. It independently recomputes Telegram text and
revision identities, compares terminal result events with their captured MT5
attempt, and rejects reused attempt IDs.

Runtime recording is asynchronous and never makes Telegram handling wait for
disk I/O. A decision-start event is queued before entering the handler and a
final action manifest is queued after it finishes. If a process or power
failure prevents either event from becoming durable, the auditor blocks that
sample instead of delaying, suppressing or repeating a live action. If a
handler fails after issuing an action, the failure event retains that
decision's exact action manifest; a later delivery can retry without erasing
the first attempt. Long-lived pending and position-monitor tasks start with a
detached causal context, then create explicit internal decisions linked back
to the signal that spawned them.

Invalid UTF-8 or malformed JSONL lines are reported as blocked source evidence
instead of being replaced or skipped. Media revisions remain intentionally
blocked until the Phase 2 media archive supplies their SHA-256; the presence of
text-only lineage does not make an unarchived image exact.

Analysis:

- `analysis/patterns.py`, `analysis/daily_report.py` and `analysis/bot_execution_quality.py` are still useful for session review.
- Many other scripts in `analysis/` are historical research helpers. Prefer the replay pipeline above for new simulation work.

## Runtime Data And Git

`main` is code-only. The tracked `data/` directory is a read-only historical
seed used during the transition; the bot never appends to it. On the first
start, `run_bot.bat` copies the complete raw seed into ignored
`runtime_data/`, preserving any existing runtime file.

As soon as the bot is active, and then every five minutes, a separate process
checkpoints complete records, verifies the SHA-256 of the complete exported
prefix and pushes immutable gzip chunks to the `telemetry` branch. It uses its
own Git checkout. A GitHub outage leaves the chunks pending locally and cannot
stop or delay Telegram/MT5 processing. The console diagnostic is stored at
`runtime_data/bot_runtime.log` and travels through the same channel.

Normal VM start:

```powershell
run_bot.bat
```

Manual local checkpoint or publication, neither of which changes `main`:

```powershell
python tools/runtime_telemetry.py --checkpoint --runtime-dir runtime_data
python tools/runtime_telemetry.py --publish-once --runtime-dir runtime_data
```

Download and verify the latest corpus on an analysis computer:

```powershell
python tools/runtime_telemetry.py --pull
```

The pull command writes only to ignored `runtime_data/`, verifies every stream,
chunk range and SHA-256 before changing the current corpus, then installs all
files with a recoverable transaction. Heavy ledger, tick, learning and
strategy-farm generation is not part of bot startup; run
`python tools/run_bot_watch.py --final-backup` only when an explicit offline
snapshot is wanted.

## Local Setup

1. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Create a local `.env` file:

   ```powershell
   python tools/setup_env.py
   ```

   The script asks for Telegram, Gemini and MT5 credentials and writes them to
   `.env`. That file is ignored by Git and must not be committed.

3. Run the production wrapper:

   ```powershell
   run_bot.bat
   ```

## Security Notes

- Never commit `.env`, `*.session`, API keys, phone numbers or MT5 credentials.
- `.env.example` is intentionally blank for sensitive values.
- `tools/parse_export.py` reads channel IDs from `.env` or from
  `--canal1-id` / `--canal2-id`; channel IDs are not hardcoded in source.

## Tests

```powershell
python -m pytest -q
```

