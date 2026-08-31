@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [%date% %time%] ERROR: .venv Python not found >> stock_trader_startup.log
  exit /b 1
)

rem Do not start a duplicate server if port 8000 is already listening.
netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [%date% %time%] INFO: Stock Trader already listening on port 8000. >> stock_trader_startup.log
  exit /b 0
)

echo [%date% %time%] Starting Stock Trader Unified UI v0.8.0 background... >> stock_trader_startup.log
".venv\Scripts\python.exe" -m uvicorn unified_app:app --host 0.0.0.0 --port 8000 >> stock_trader_server.log 2>&1
