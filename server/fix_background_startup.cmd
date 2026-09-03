@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
if not exist "server\.venv\Scripts\python.exe" (
  echo [ERROR] server\.venv\Scripts\python.exe not found.
  pause
  exit /b 10
)

echo Stock Day Trader quiet-start repair

echo 1. Removing legacy supervisor/watchdog/startup entries...
"server\.venv\Scripts\python.exe" "server\install_autostart_task.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo [ERROR] quiet autostart install failed. code=%RC%
  echo Try running this file once as Administrator.
  pause
  exit /b %RC%
)

echo 2. Quiet autostart installed successfully.
echo 3. Reboot Windows once to terminate any already-running legacy PowerShell supervisor.
echo.
echo After reboot, StockTraderAutoStart will run hidden via pythonw.exe.
echo The normal Stock Trader server does not need a visible PowerShell window.
pause
endlocal
exit /b 0
