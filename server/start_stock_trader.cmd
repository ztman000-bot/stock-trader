@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Stock Trader Server v0.17.8

if not exist ".venv\Scripts\python.exe" (
 echo [ERROR] .venv Python not found.
 exit /b 1
)

echo Starting through canonical localhost-only background launcher...
call "start_stock_trader_background.cmd"
".venv\Scripts\python.exe" health_probe.py full >nul 2>&1
if errorlevel 1 (
 echo [WARN] Server/UI health is not ready yet. Watchdog will continue recovery.
 exit /b 6
)
echo [OK] Stock Trader API/UI ONLINE. Background recovery is windowless.
endlocal
exit /b 0
