# Runtime, telemetry and replay preparation

Read for live behavior, capture, operational fixes or preparing a replay.
This is a navigation reference, not a command to run every stage on every task.

## Runtime boundaries

- run_bot.bat: Windows launcher.
- tools/run_bot_watch.py: code-update supervision, runtime/report coordination
  and telemetry handoff. A published code update may lead to a restart.
- main.py: Telegram/MT5 bootstrap, resynchronization and monitors.
- mt5_client.py / mt5_worker_entry.py: direct ownership of the isolated worker.
  mt5_worker_lifetime.py guards parent death using pinned OS process references;
  do not replace this with PID-only cleanup or terminal process-tree kills.
  Windows venv launches must bypass the redirector while preserving the venv.
  Async deadlines require Python >= 3.11 and preserve external cancellation.
  Trade cancellation competes with authorization after the final guard using
  a short local lock, never held during storage or IPC. Once authorized,
  cancellation abandons the caller's wait, not the broker operation: retain
  ownership until completion/reconciliation and never infer permission to retry.
- listener.py, parser.py, classifier.py: interpretation and routing.
- executor.py: actual MT5 open/modify/close boundary.
- position_lifecycle_monitor.py: BE, time-stop, finalization and leftovers;
  do not confuse it with historical dca_monitor.py references.
- state.py, journal.py: signal state and event recording.
  Durable entry admission confirms the exact journal request before transport;
  journal failures veto new exposure, not ledger-protected management. Cached
  disk health expires; optional snapshot gaps and paused shadows remain explicit.
  A successful flush does not clear a session's lost-evidence safety latch.
- pending_actions.py: retries; live_auditor.py: consistency with MT5.
- strategies.py: strategy guards. Observe deployed configuration before stating
  what any channel currently does.

Offline research and publication must not be imported into a live order module.
In particular keep provider_trade_spec, provider_strategy_simulator,
strategy_farm, simulation_run_provenance and recursive_log_learning out of
live order paths. Shared pure strategy contracts do not authorize online search.

## Source and publication

Resolve paths through runtime_paths.py and the active BOT_RUNTIME_DATA_DIR.
The default runtime directory is runtime_data; explicit legacy/research inputs
can still reside in data. Do not assume the current VM's output is a tracked
data file, or delete historical versioned evidence.

Use the existing runtime_telemetry checkpoint/outbox and publication workflow.
Do not automatically stage runtime catalogs and reports into main as part of
normal log collection. Intentional versioned research artifacts are separate.
Preserve raw events, source hashes, retention and recoverability; derived reports
are not a replacement for original logs or tick caches.

Use tools/analyze_new_logs.py for normal incremental review. Prefer existing
indexed summaries before broad raw scans; expand to exact raw evidence for a
finding. No extra whole-corpus scan or heavy analysis in the live process.

## Replay dependencies

Inspect CLI options and current orchestrator before a run. Use the resolved
input/output directory consistently; do not rerun unchanged expensive stages.

1. reconcile_mt5_ledger.py builds ledger.jsonl.
2. build_replay_trades.py builds replay_trades.jsonl and its deterministic
   replay_trades.jsonl.manifest.json source contract.
3. accounting_replay_validator.py checks observed money.
4. tools/ensure_replay_tick_cache.py checks/prepares tick evidence.
5. replay_readiness_report.py and observed_tick_replay_validator.py report
   readiness and tick replay; respect orchestrator dependencies and freshness.
6. provider_signal_catalog.py supplies the canonical provider timeline.
7. The chosen strategy/research engine consumes verified inputs.
8. strategy_farm.py, recursive_log_learning.py and simulation_run_provenance.py
   produce their applicable offline outputs; not every query needs this chain.

mt5_tick_cache.py accepts exact replay only with a matching
mt5_server_epoch_utc_v3 sidecar, hash and semantic time/anchor validation.
V1/V2 caches are diagnostic-only and need proper regeneration, not relabeling.

replay_source_contract.py binds replay to exact ledger and raw event hashes.
A missing/stale contract stops the affected farm run.

## Gold zone lifecycle

canal2_zone_lifecycle.py defines pure zone rules. Check actual deployment flags
before calling zones executable. Complete single-zone capability is distinct
from multi-zone/incomplete observation; do not enable either implicitly.
First touch is Ask for BUY and Bid for SELL.

- Keep NOW behavior independent and compatible.
- One Telegram identity confirms at most one MT5 exposure generation.
  Explicit re-entry needs a new message identity and generation.
- Replies alias the original plan. Applied TP/SL updates also update the retained
  plan for any later authorized re-entry.
- Restore only schema-v2 plans. Triggered plans remain reply context after a
  restart; legacy observation rows never become executable.
- Preserve entry_source_kind, root/zone IDs, generation, trigger side/price and
  broker time_msc as entry_provenance through reconciliation and replay.

## Session totals

analysis/daily_report.py distinguishes signal-cohort P/L from MT5 server-calendar
close totals. Currency comes from mt5_account_connected evidence, not inference.
analysis/patterns.py and analysis/bot_execution_quality.py can support diagnosis;
other historical scripts need deliberate suitability checks before reuse.
