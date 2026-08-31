@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Stock Trader - Install Supervisor v0.12.0

if not exist "server\.venv\Scripts\python.exe" (echo [ERROR] server\.venv Python not found.&pause&exit /b 1)
if not exist "server\supervisor_stock_trader.cmd" (echo [ERROR] supervisor script not found.&pause&exit /b 1)

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%STARTUP%\StockTraderWatchdog.vbs" del /q "%STARTUP%\StockTraderWatchdog.vbs" >nul 2>&1
if exist "%STARTUP%\StockTraderAutoStart.vbs" del /q "%STARTUP%\StockTraderAutoStart.vbs" >nul 2>&1

echo [1/4] Installing Windows Task Scheduler supervisor...
schtasks /Delete /TN "StockTraderWatchdog" /F >nul 2>&1
schtasks /Delete /TN "StockTraderAutoStart" /F >nul 2>&1
schtasks /Delete /TN "StockTraderSupervisor" /F >nul 2>&1
schtasks /Create /TN "StockTraderSupervisor" /SC MINUTE /MO 1 /TR "cmd.exe /c \"%CD%\server\supervisor_stock_trader.cmd\"" /F >nul 2>&1
if errorlevel 1 (echo [ERROR] Could not create StockTraderSupervisor task.&echo [INFO] Run this installer with Administrator privileges.&pause&exit /b 2)

rem Do not disable recovery just because AC power is removed.
powershell -NoProfile -Command "$t=Get-ScheduledTask -TaskName 'StockTraderSupervisor'; $s=$t.Settings; $s.DisallowStartIfOnBatteries=$false; $s.StopIfGoingOnBatteries=$false; Set-ScheduledTask -TaskName 'StockTraderSupervisor' -Settings $s | Out-Null" >nul 2>&1

echo [OK] StockTraderSupervisor scheduled every 1 minute, including battery power.
echo [2/4] Starting supervisor now...
schtasks /Run /TN "StockTraderSupervisor" >nul 2>&1
timeout /t 5 /nobreak >nul

echo [3/4] Checking server + UI health...
powershell -NoProfile -Command "try { $h=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 4; $u=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/system/ui-health' -TimeoutSec 4; if($h.ok -and $u.ok){Write-Host ('[OK] Server/UI ONLINE - engine v' + $h.version + ' / UI ' + $u.uiVersion); exit 0}else{exit 1} } catch { Write-Host '[WARN] Initial health check not ready; scheduled supervisor will retry.'; exit 0 }"
echo [4/4] Verifying scheduled task...
schtasks /Query /TN "StockTraderSupervisor" >nul 2>&1
if errorlevel 1 (echo [ERROR] Supervisor task verification failed.&pause&exit /b 3)
echo [OK] Supervisor task installed and verified.
echo [INFO] Tailscale remains managed by its Windows app/service.
echo.
echo INSTALL COMPLETE - recovery remains enabled on AC or battery while Windows is running.
pause
endlocal
