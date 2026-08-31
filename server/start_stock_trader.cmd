@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [%date% %time%] ERROR: .venv Python not found >> stock_trader_startup.log
  exit /b 1
)
echo [%date% %time%] Starting Stock Trader v0.7.4... >> stock_trader_startup.log
".venv\Scripts\python.exe" -m uvicorn app:app --host 0.0.0.0 --port 8000 >> stock_trader_server.log 2>&1
