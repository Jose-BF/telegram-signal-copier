@echo off
cd /d "%~dp0"
start "VT Markets BTCUSD" cmd /k python measure_broker_latency.py --symbol BTCUSD --login 1344177
timeout /t 3 >nul
start "Vantage BTCUSD" cmd /k python measure_broker_latency.py --symbol BTCUSD --login 24767476
