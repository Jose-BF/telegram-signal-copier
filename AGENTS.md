# AGENTS.md - telegram-signal-copier

Project: Telegram signal copier for MT5, focused on exact replay and simulation readiness.

Use these current file names when navigating the repo. Some historical docs may mention older names; prefer the names below for all new work.

## Runtime Entry Points

- `run_bot.bat`: Windows production launcher.
- `tools/run_bot_watch.py`: watcher that pulls code, restarts the bot, regenerates reports and pushes session data.
- `main.py`: runtime bootstrap for Telegram, MT5, resync and monitors.
- `listener.py`: Telegram message interpretation and signal/management routing.
- `executor.py`: MT5 order open/modify/close operations.
- `position_lifecycle_monitor.py`: open-position lifecycle monitor. It handles BE, time-stop, auto-finalize and leftover position handling. This replaces the old mental model/name `dca_monitor.py`.

## Replay Pipeline

Run order:

1. `reconcile_mt5_ledger.py` -> `data/ledger.jsonl`
2. `build_replay_trades.py` -> `data/replay_trades.jsonl`
3. `accounting_replay_validator.py` -> `data/accounting_replay_audit.jsonl`
4. `tools/ensure_replay_tick_cache.py` -> `data/replay_tick_cache_status.json`
5. `replay_readiness_report.py` -> `data/replay_readiness_report.json`
6. `observed_tick_replay_validator.py` -> `data/observed_tick_replay_audit.jsonl`
7. `provider_signal_catalog.py` -> `data/provider_signal_catalog.json`
8. `strategy_farm.py` -> `data/strategy_farm.json`

## Support Modules

- `state.py`: in-memory signal model and helpers.
- `journal.py`: event-sourced JSONL/CSV logging.
- `pending_actions.py`: retry queue for MT5 actions.
- `live_auditor.py`: runtime consistency checks against MT5.
- `parser.py`: rule-based signal parsing.
- `classifier.py`: Gemini/regex message classification.
- `strategies.py`: current strategy guards and helper decisions.
- `mt5_tick_cache.py`: parquet cache helper for MT5 ticks. Cached days are valid only with the matching UTC-v2 SHA-256 sidecar written by `tools/ensure_replay_tick_cache.py`.
- `strategy_policies.py`: declarative close/BE/runner policy catalog shared by both channels.
- `strategy_simulator.py`: causal tick replay for one management policy. Farm runs must use canonical provider timelines, never MT5 ticket histories as a substitute.
- `strategy_farm.py`: batch policy comparison and strict selection gates.

## Analysis Guidance

- Prefer the replay pipeline for new simulation work.
- `analysis/patterns.py`, `analysis/daily_report.py` and `analysis/bot_execution_quality.py` remain useful for manual session review.
- Treat most other `analysis/` scripts as historical research until deliberately promoted or removed.

## Verification

Before claiming a refactor is safe, run:

```powershell
python -m pytest -q
```
