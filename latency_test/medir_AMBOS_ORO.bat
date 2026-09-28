@echo off
cd /d "%~dp0"
start "VT Markets XAUUSD" cmd /k python measure_broker_latency.py --symbol XAUUSD --login 1344177
timeout /t 3 >nul
start "Vantage XAUUSD" cmd /k python measure_broker_latency.py --symbol XAUUSD --login 24767476
