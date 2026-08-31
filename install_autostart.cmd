@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Stock Trader - Install Autostart
if not exist "server\.venv\Scripts\python.exe" (echo [ERROR] server\.venv Python not found.&pause&exit /b 1)
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS=%STARTUP%\StockTraderAutoStart.vbs"
set "WATCHVBS=%STARTUP%\StockTraderWatchdog.vbs"
> "%VBS%" echo Set sh = CreateObject("WScript.Shell")
>> "%VBS%" echo sh.Run Chr(34) ^& "%CD%\server\start_stock_trader_background.cmd" ^& Chr(34), 0, False
> "%WATCHVBS%" echo Set sh = CreateObject("WScript.Shell")
>> "%WATCHVBS%" echo sh.Run Chr(34) ^& "%CD%\server\watchdog_stock_trader.cmd" ^& Chr(34), 0, False
echo [OK] Stock Trader background autostart installed.
echo [OK] 60-second health watchdog installed.
echo [INFO] Tailscale remains managed by its Windows app/service.

rem Start server now if needed.
start "" wscript.exe "%VBS%"
timeout /t 3 /nobreak >nul

rem Start watchdog in this Windows session too. The watchdog owns a singleton lock,
rem so running this installer repeatedly is safe.
start "" wscript.exe "%WATCHVBS%"
timeout /t 3 /nobreak >nul

powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 4; Write-Host ('[OK] Server ONLINE - v' + $r.version) } catch { Write-Host '[WARN] Health check failed. Watchdog will retry.' }"
powershell -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter \"Name='wscript.exe'\" | Where-Object { $_.CommandLine -like '*StockTraderWatchdog.vbs*' }; if($p){ Write-Host ('[OK] Watchdog ACTIVE - PID ' + (($p.ProcessId -join ','))) } else { Write-Host '[WARN] Watchdog process not detected.' }"
echo.
echo INSTALL COMPLETE - server + watchdog active now and after Windows login.
pause
endlocal
