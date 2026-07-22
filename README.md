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
- `build_replay_trades.py` builds `runtime_data/replay_trades.jsonl` from ledger and event history.
- `accounting_replay_validator.py` validates reconstructed trade accounting into `runtime_data/accounting_replay_audit.jsonl`.
- `tools/ensure_replay_tick_cache.py` ensures MT5 tick parquet files exist and verifies the `mt5_server_epoch_utc_v3` time/anchor contract and SHA-256 for every cached day.
- `replay_readiness_report.py` reports whether each trade has enough data for full replay.
- `observed_tick_replay_validator.py` checks whether cached bid/ask ticks reproduce the observed MT5 ticket closures.
- `mt5_tick_cache.py` is the local parquet tick-cache helper.
- `provider_signal_catalog.py` groups raw Telegram messages and edits into one canonical provider signal, including signals the bot did not execute.
- `provider_trade_spec.py` turns every formal provider signal into an immutable virtual-trade contract without requiring an MT5 ticket.
- `provider_strategy_simulator.py` enters BUY at Ask or SELL at Bid after the configured causal latency, then replays policy price paths over verified ticks.
- `strategy_policies.py` defines the shared close/BE/runner policy matrix for both channels.
- `strategy_simulator.py` retains observed-ticket replay as an independent execution control.
- `strategy_farm.py` evaluates every formal Telegram signal, including unexecuted signals, once per policy and ordered latency scenario. It also keeps observed-ticket validation in a separate section.
- `simulation_run_provenance.py` fingerprints the exact selected farm inputs, policy order, source files, runtime versions and tick contracts already verified by the replay loader.
- `runtime_data/simulation_runs/<fingerprint>/run_card.json` is immutable run evidence. Repeating identical computational inputs reuses the same directory; a contradictory result fails closed.

`runtime_data/provider_signal_catalog.json` is a canonical, versioned farm input. It
must travel with `runtime_data/strategy_farm.json` and the corresponding run card; it
is not a disposable intermediate report.

Run the current clean-window diagnostics manually:

```powershell
python provider_signal_catalog.py
python strategy_farm.py --from 2026-07-06
python strategy_farm.py --from 2026-07-06 --provider-latency-ms 0 --provider-latency-ms 150 --provider-latency-ms 250
python strategy_farm.py --from 2026-07-06 --include-trades --output runtime_data/strategy_farm_detail.json
```

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

`provider_scope.rows_expected` must equal `rows_emitted`, and
`signals_omitted` must be empty. Missing ticks or causal levels create visible
blocked rows for the affected signal/policy pair; they never remove the signal.

Provider-first `strategy_value` is directional XAUUSD price movement summed
across virtual legs. It is not account-currency P&L and must not be interpreted
as profitability. `selection.selected_policy` remains `null`, monetary ranking
remains empty and `broker_money_contract_unverified` remains visible until MT5
gross/net P&L can be reproduced to the cent with conversion, commission and
swap evidence. Baseline replay requires verifiable bid/ask
ticks at both the MT5 entry and exit, matching first-touch time, reason and
fill price. Blocked tick replay, small samples and an unvalidated untouched OOS
period also prevent strategy selection.

Tick run-card digests prove which local Parquet bytes were used at execution
time. The Parquet cache itself is currently local-only: a run card cannot
recreate a cache file after that file has been deleted. Such cards therefore
report current verification separately from durable artifact retention.

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

