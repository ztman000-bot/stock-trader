@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Stock Trader - Quiet Autostart Installer v0.17.8

if not exist "server\.venv\Scripts\python.exe" (echo [ERROR] server\.venv Python not found.&pause&exit /b 1)
if not exist "server\install_autostart_task.py" (echo [ERROR] installer helper not found.&pause&exit /b 1)

echo [1/3] Removing legacy every-minute supervisor / watchdog tasks...
"server\.venv\Scripts\python.exe" "server\install_autostart_task.py"
if errorlevel 1 (
 echo [ERROR] Quiet autostart task installation failed.
 echo [INFO] If Windows blocks Task Scheduler changes, run this file as Administrator once.
 pause
 exit /b 2
)

echo [2/3] Waiting for hidden autostart...
timeout /t 5 /nobreak >nul

echo [3/3] Checking server + UI health without PowerShell...
"server\.venv\Scripts\python.exe" "server\health_probe.py" full >nul 2>&1
if errorlevel 1 (
 echo [WARN] Initial health is not ready yet. The pythonw watchdog will keep retrying in background.
) else (
 echo [OK] Server/UI ONLINE.
)

echo.
echo INSTALL COMPLETE
 echo - Old 1-minute StockTraderSupervisor task removed
 echo - One hidden StockTraderAutoStart task installed
 echo - Background recovery uses pythonw watchdog only
 echo - No recurring PowerShell health checks
pause
endlocal
