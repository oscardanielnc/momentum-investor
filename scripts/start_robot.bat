@echo off
title investor - ROBOT (demo paper)
cd /d "D:\OSCAR\Documents\Trading Proyects\investor"
set INVESTOR_DRY_RUN=false
set INVESTOR_ALPACA_LIVE=false
set INVESTOR_HEARTBEAT_S=900
echo ============================================================
echo   investor - ROBOT en DEMO (paper)
echo   Heartbeat cada 15 min + rebalanceo diario
echo   Cerrar esta ventana o Ctrl+C para parar
echo ============================================================
python engine\orchestrator.py --loop
echo.
echo El robot se detuvo. Pulsa una tecla para cerrar.
pause >nul
