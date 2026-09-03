@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
if not exist "server\.venv\Scripts\python.exe" (
  echo [ERROR] server\.venv\Scripts\python.exe not found.
  pause
  exit /b 10
)

echo Stock Day Trader DEEP quiet-start repair
echo.
echo 1. Removing duplicate Stock Trader scheduled tasks, Startup items and Run entries...
"server\.venv\Scripts\python.exe" "server\install_autostart_task.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo [ERROR] quiet autostart repair failed. code=%RC%
  echo Run this file once with ^"Run as administrator^".
  echo Log: %%TEMP%%\stock_trader_startup_cleanup.log
  pause
  exit /b %RC%
)

echo 2. Exactly one hidden StockTraderAutoStart task was installed.
echo 3. Automatic boot/recovery now uses pythonw + silent_boot.py; no findstr window is required.
echo 4. Reboot Windows once to terminate already-running legacy cmd/PowerShell processes.
echo.
echo [OK] DEEP QUIET-START REPAIR COMPLETE
echo Log: %%TEMP%%\stock_trader_startup_cleanup.log
pause
endlocal
exit /b 0
