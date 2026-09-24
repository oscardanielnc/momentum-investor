@echo off
title investor - ROBOT (paper)
cd /d "%~dp0.."
set INVESTOR_DRY_RUN=false
set INVESTOR_ALPACA_LIVE=false
set INVESTOR_HEARTBEAT_S=900
echo ============================================================
echo   investor - ROBOT on the PAPER account
echo   Heartbeat every 15 min + daily rebalance check
echo   Close this window or press Ctrl+C to stop
echo ============================================================
python engine\orchestrator.py --loop
echo.
echo The robot stopped. Press any key to close.
pause >nul
