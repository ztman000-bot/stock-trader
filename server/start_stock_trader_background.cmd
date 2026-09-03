@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\StockTrader\laptop_server_paused.flag" exit /b 0
if not exist ".venv\Scripts\pythonw.exe" exit /b 1
rem Compatibility wrapper only. The real bootstrap is Python/windowless.
start "" /b ".venv\Scripts\pythonw.exe" "silent_boot.py" >nul 2>&1
endlocal
exit /b 0
