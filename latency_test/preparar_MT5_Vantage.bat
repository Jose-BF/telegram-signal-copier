@echo off
echo Copiando MetaTrader 5 a C:\MT5_Vantage_test ...
xcopy "C:\Program Files\MetaTrader 5" "C:\MT5_Vantage_test\" /E /I /Y /Q
echo Abriendo la copia (portable). Inicia sesion con la NUEVA demo de Vantage.
start "" "C:\MT5_Vantage_test\terminal64.exe" /portable
pause
