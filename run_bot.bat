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
REM Lanzamos tools\run_bot_watch.py en vez de main.py directo. El watcher
REM arranca codigo local verificado aunque GitHub no responda. Los datos raw
REM se guardan primero en un checkpoint local y se publican despues, sin
REM ejecutar informes o simulaciones pesadas en el camino de reinicio.
powershell -NoProfile -Command "[Console]::OutputEncoding = [Text.Encoding]::UTF8; python -u tools\run_bot_watch.py 2>&1 | Tee-Object -FilePath logs\bot_runtime.log -Append; exit $LASTEXITCODE"
set EXITCODE=%errorlevel%

echo.
echo [%date% %time%] Watcher terminado. Exit code: %EXITCODE%
echo [%date% %time%] === SALIDA codigo=%EXITCODE% === >> logs\bot_runtime.log

REM El watcher guarda solo la evidencia raw en el camino de reinicio. Los
REM informes y simulaciones pesadas se regeneran fuera del camino critico.
if "%EXITCODE%"=="77" goto retry_wait
if "%EXITCODE%"=="78" goto duplicate_exit
if "%EXITCODE%"=="0" goto restart_wait
if "%EXITCODE%"=="75" goto restart_wait

python -u tools\run_bot_watch.py --recovery-checkpoint
set BACKUPCODE=%errorlevel%
if "%BACKUPCODE%"=="77" goto retry_wait
if not "%BACKUPCODE%"=="0" goto backup_failed
goto restart_wait

:backup_failed
echo [Watch] Estado Git bloqueado. Los datos siguen preservados; no arranco codigo sin verificar.
exit /b 76

:retry_wait
echo.
echo [Watch] Fallo temporal de red/Git. Reintento seguro en 60 segundos...
timeout /t 60 /nobreak > nul
goto restart

:duplicate_exit
echo.
echo [Watch] Ya hay otro supervisor activo. Cierro esta instancia duplicada.
exit /b 78

:restart_wait

echo.
echo Reiniciando en 10 segundos...
echo (cierra la ventana para parar definitivamente)
timeout /t 10 /nobreak > nul

goto restart
