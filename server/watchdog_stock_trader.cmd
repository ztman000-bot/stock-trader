@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "LOG=stock_trader_watchdog.log"
:loop
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 4 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo [%date% %time%] Health failed. Restarting Stock Trader... >> "%LOG%"
  start "" /min cmd /c "start_stock_trader_background.cmd"
  timeout /t 15 /nobreak >nul
) else (
  timeout /t 60 /nobreak >nul
)
goto loop
