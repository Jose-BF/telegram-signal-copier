@echo off
cd /d "%~dp0"
python measure_broker_latency.py --symbol BTCUSD --terminal "C:\MT5_Vantage_test\terminal64.exe"
pause
