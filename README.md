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

Alternative positions that cross the broker calendar rollover are also
blocked from money totals while the contract remains
`intraday_only_zero`. Their price outcome stays available for diagnosis, but
the simulator will not present P&L that omits swap.

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

