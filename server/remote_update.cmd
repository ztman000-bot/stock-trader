@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
if not exist "server\.venv\Scripts\python.exe" exit /b 10
"server\.venv\Scripts\python.exe" "server\atomic_update.py"
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
