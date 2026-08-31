@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "LOG=%TEMP%\stock_trader_watchdog.log"

:loop
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 4 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo [%date% %time%] Health failed; requesting serialized restart. >> "%LOG%"
  rem Call the race-safe launcher. Its mutex prevents duplicate uvicorn starts
  rem when update/autostart/watchdog overlap.
  call "start_stock_trader_background.cmd"
  timeout /t 20 /nobreak >nul
) else (
  timeout /t 60 /nobreak >nul
)
goto loop
