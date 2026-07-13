# telegram-signal-copier

Telegram signal copier for MetaTrader 5.

## Project Map

Production runtime:

- `run_bot.bat` starts the Windows watcher loop.
- `tools/run_bot_watch.py` keeps the bot updated, restarts it, regenerates reports and pushes session data.
- `main.py` starts Telegram, MT5, reconciliation/resync and runtime monitors.
- `listener.py` interprets Telegram messages and routes signal/management actions.
- `executor.py` sends and modifies MT5 orders.
- `position_lifecycle_monitor.py` watches open signal lifecycle: BE, time-stop, auto-finalize and leftover position handling. This was formerly the DCA monitor; do not use the old name for new work.
- `state.py`, `journal.py`, `pending_actions.py`, `live_auditor.py`, `strategies.py`, `parser.py`, `classifier.py` are runtime support modules.

Replay and simulation foundation:

- `reconcile_mt5_ledger.py` rebuilds `data/ledger.jsonl` from bot logs plus MT5 history.
- `build_replay_trades.py` builds `data/replay_trades.jsonl` from ledger and event history.
- `accounting_replay_validator.py` validates reconstructed trade accounting into `data/accounting_replay_audit.jsonl`.
- `tools/ensure_replay_tick_cache.py` ensures MT5 tick parquet files exist and verifies a UTC-v2 SHA-256 manifest for every cached day.
- `replay_readiness_report.py` reports whether each trade has enough data for full replay.
- `observed_tick_replay_validator.py` checks whether cached bid/ask ticks reproduce the observed MT5 ticket closures.
- `mt5_tick_cache.py` is the local parquet tick-cache helper.
- `provider_signal_catalog.py` groups raw Telegram messages and edits into one canonical provider signal, including signals the bot did not execute.
- `strategy_policies.py` defines the shared close/BE/runner policy matrix for both channels.
- `strategy_simulator.py` replays one causal management policy over validated ticks and canonical Telegram level/management timelines.
- `strategy_farm.py` compares the policy matrix and writes `data/strategy_farm.json` with metrics, coverage and strict selection blockers.
- `simulation_run_provenance.py` fingerprints the exact selected farm inputs, policy order, source files, runtime versions and tick contracts already verified by the replay loader.
- `data/simulation_runs/<fingerprint>/run_card.json` is immutable run evidence. Repeating identical computational inputs reuses the same directory; a contradictory result fails closed.

`data/provider_signal_catalog.json` is a canonical, versioned farm input. It
must travel with `data/strategy_farm.json` and the corresponding run card; it
is not a disposable intermediate report.

Run the current clean-window diagnostics manually:

```powershell
python provider_signal_catalog.py
python strategy_farm.py --from 2026-07-06
python strategy_farm.py --from 2026-07-06 --include-trades --output data/strategy_farm_detail.json
```

Use `--run-archive-dir <path>` to override the default
`data/simulation_runs` archive. Compact farm runs retain a copy of the report
inside their fingerprint directory. `--include-trades` reports can be large,
so the first-published output path, exact size and SHA-256 are recorded but the
report is not copied into the archive.

`--ensure` automatically replaces legacy, unversioned or tampered cache days.
`--refresh-day` remains available when a known day must be forced manually:

```powershell
python tools/ensure_replay_tick_cache.py --ensure --refresh-day 2026-07-08 --refresh-day 2026-07-09 --refresh-day 2026-07-10
```

`selection.selected_policy` remains `null` while any strict gate is open.
Policies with missing trades or estimated monetary conversion are excluded
from `exploratory_ranking` as well. Baseline replay requires verifiable bid/ask
ticks at both the MT5 entry and exit, matching first-touch time, reason and
fill price. Missing provider signals, blocked tick replay, small samples and an
unvalidated untouched OOS period prevent strategy selection.

Tick run-card digests prove which local Parquet bytes were used at execution
time. The Parquet cache itself is currently local-only: a run card cannot
recreate a cache file after that file has been deleted. Such cards therefore
report current verification separately from durable artifact retention.

Analysis:

- `analysis/patterns.py`, `analysis/daily_report.py` and `analysis/bot_execution_quality.py` are still useful for session review.
- Many other scripts in `analysis/` are historical research helpers. Prefer the replay pipeline above for new simulation work.

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

3. Run the bot:

   ```powershell
   python main.py
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

