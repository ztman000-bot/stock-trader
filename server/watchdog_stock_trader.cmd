@echo off
setlocal EnableExtensions
cd /d "%~dp0"
rem Legacy entrypoint retained for compatibility. The old infinite CMD/PowerShell
rem loop is removed; start one windowless Python watchdog and exit immediately.
if exist ".venv\Scripts\pythonw.exe" start "" /b ".venv\Scripts\pythonw.exe" watchdog.py >nul 2>&1
endlocal
exit /b 0
