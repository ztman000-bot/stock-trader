@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Stock Trader Server v0.11.1

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv Python not found.
  exit /b 1
)

rem Foreground/manual start deliberately does NOT redirect stdout to the shared
rem stock_trader_server.log. Multiple cmd processes opening that file caused
rem Windows ERROR_SHARING_VIOLATION during update/watchdog races.
netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [INFO] Stock Trader is already listening on port 8000.
  exit /b 0
)

echo Starting Stock Trader v0.11.1...
".venv\Scripts\python.exe" -m uvicorn unified_app:app --host 0.0.0.0 --port 8000
set "RC=%ERRORLEVEL%"
echo.
echo Stock Trader server stopped. exit=%RC%
exit /b %RC%
