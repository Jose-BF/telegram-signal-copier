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
- `tools/ensure_replay_tick_cache.py` ensures MT5 tick parquet files exist for replay windows.
- `replay_readiness_report.py` reports whether each trade has enough data for full replay.
- `observed_tick_replay_validator.py` checks whether cached bid/ask ticks reproduce the observed MT5 ticket closures.
- `mt5_tick_cache.py` is the local parquet tick-cache helper.

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

