@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "server\.venv\Scripts\python.exe" (
  echo [ERROR] server\.venv\Scripts\python.exe not found.
  pause
  exit /b 10
)
echo Switching laptop to PHONE SERVER MODE...
"server\.venv\Scripts\python.exe" "server\host_role.py" pause
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [OK] Laptop Stock Trader server is paused.
  echo You may keep using this laptop normally while the spare phone is the server.
) else (
  echo [WARN] Pause helper returned code %RC%.
  echo If Task Scheduler access was blocked, run this file once as Administrator.
)
pause
endlocal & exit /b %RC%
