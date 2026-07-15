@echo off
REM ============================================================
REM   Signal Copier Bot - arranque con auto-restart + logs
REM
REM   USO: doble click en este .bat
REM
REM   Para PARAR el bot: cierra la ventana del cmd directamente
REM   (la X de la esquina). Ctrl+C reinicia el loop.
REM
REM   Logs: logs\bot_runtime.log
REM ============================================================

chcp 65001 > nul
title Signal Copier Bot - %date% %time%
cd /d "%~dp0"

if not exist logs mkdir logs

:restart
echo.
echo ====================================================
echo   Arrancando bot: %date% %time%
echo ====================================================
echo.

echo. >> logs\bot_runtime.log
echo [%date% %time%] === ARRANQUE === >> logs\bot_runtime.log

REM Tee: muestra en pantalla EN VIVO Y guarda en logs\bot_runtime.log al mismo
REM tiempo. Detalles de cada flag:
REM   • python -u                : sin buffer (cada print aparece al instante,
REM                                no en bloques cuando se llena el buffer)
REM   • 2>&1                     : stderr (errores) tambien va al pipe, no
REM                                solo stdout
REM   • Tee-Object -Append       : copia a archivo Y vuelve a sacar al stdout,
REM                                que es la consola visible
REM   • exit $LASTEXITCODE       : devuelve el codigo de salida REAL de Python
REM                                (sin esto recogeriamos el codigo de PS, que
REM                                siempre es 0 si el pipe no rompio)
REM   • [Console]::OutputEncoding=UTF8 : evita que acentos y emojis salgan
REM                                como ? en la consola al pasar por PS
REM
REM CAMBIO: lanzamos tools\run_bot_watch.py en vez de main.py directo. El
REM watcher hace pull automatico al detectar commits nuevos en origin/main
REM y reinicia main.py — asi nunca volvemos a quedarnos con codigo viejo
REM como paso el 2026-05-06 (115min congelado por bug ya parcheado pero
REM no aplicado). El watcher tambien hace git push de datos cada vez que
REM reinicia main.py, asi que ya no necesitamos el bloque git de abajo.
powershell -NoProfile -Command "[Console]::OutputEncoding = [Text.Encoding]::UTF8; python -u tools\run_bot_watch.py 2>&1 | Tee-Object -FilePath logs\bot_runtime.log -Append; exit $LASTEXITCODE"
set EXITCODE=%errorlevel%

echo.
echo [%date% %time%] Watcher terminado. Exit code: %EXITCODE%
echo [%date% %time%] === SALIDA codigo=%EXITCODE% === >> logs\bot_runtime.log

REM El watcher ya sube los datos en cada reinicio. Aqui solo hacemos un
REM "ultimo backup" por si murio sin oportunidad de subir.
python -c "import tools.run_bot_watch as w; w._clear_mutable_offline_outputs(); ok=w._regenerate_ledger(); replay=w._regenerate_replay_trades() if ok else False; audit=w._regenerate_accounting_replay_audit() if replay else False; w._regenerate_replay_tick_cache_status() if audit else False; observed=w._regenerate_observed_tick_replay_audit() if audit else False; w._regenerate_replay_readiness_report() if audit else False; catalog=w._regenerate_provider_signal_catalog() if replay else False; w._regenerate_strategy_farm() if observed and catalog else False; raise SystemExit(0 if ok and replay and audit else 1)" 2>nul
if errorlevel 1 echo [Watch] backup final fallo; ledger/replay/audit pueden quedar desfasados.
git add -f data\trade_events.jsonl data\trade_events_TEST.jsonl 2>nul
git add -f data\trade_journal.csv data\trade_journal_TEST.csv 2>nul
git add -f data\ledger.jsonl data\reconcile_status.json 2>nul
git add -f data\replay_trades.jsonl data\replay_status.json 2>nul
git add -f data\accounting_replay_audit.jsonl data\accounting_replay_audit_status.json 2>nul
git add -f data\replay_tick_cache_status.json data\replay_readiness_report.json 2>nul
git add -f data\observed_tick_replay_audit.jsonl data\observed_tick_replay_status.json 2>nul
git add -f data\provider_signal_catalog.json data\strategy_farm.json 2>nul
if exist data\simulation_runs git add -f data\simulation_runs 2>nul
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "data: sesion %date% %time% (watcher exit)" 2>nul
    git pull --rebase origin main 2>nul
    git push origin main 2>nul
)

echo.
echo Reiniciando en 10 segundos...
echo (cierra la ventana para parar definitivamente)
timeout /t 10 /nobreak > nul

goto restart
