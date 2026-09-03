@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "server\.venv\Scripts\python.exe" (
  echo [ERROR] server\.venv\Scripts\python.exe not found.
  pause
  exit /b 10
)
echo Switching laptop back to LAPTOP SERVER MODE...
echo IMPORTANT: stop the Android phone server first.
"server\.venv\Scripts\python.exe" "server\host_role.py" resume
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [OK] Laptop Stock Trader server is active again.
) else (
  echo [ERROR] Resume helper returned code %RC%.
  echo If Task Scheduler access was blocked, run this file once as Administrator.
)
pause
endlocal & exit /b %RC%
