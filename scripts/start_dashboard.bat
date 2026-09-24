@echo off
title investor - Dashboard
cd /d "%~dp0.."
python dashboard\server.py
echo.
echo The dashboard stopped. Press any key to close.
pause >nul
