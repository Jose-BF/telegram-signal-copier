@echo off
cd /d "%~dp0"
python measure_broker_latency.py --symbol BTCUSD --terminal "C:\Program Files\MetaTrader 5\terminal64.exe"
pause
