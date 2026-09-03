@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "server\.venv\Scripts\python.exe" (
  echo [ERROR] server\.venv\Scripts\python.exe not found.
  pause
  exit /b 10
)
"server\.venv\Scripts\python.exe" "server\export_phone_state.py"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [OK] Copy the phone_transfer folder to the Android phone.
  echo [SAFE] NH credentials and server\.env were NOT exported.
) else (
  echo [ERROR] export failed. code=%RC%
)
pause
endlocal & exit /b %RC%
