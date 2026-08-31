@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "LOG=%TEMP%\stock_trader_watchdog.log"
set "LOCK=%TEMP%\stock_trader_watchdog.lock"

rem Singleton watchdog: mkdir is atomic enough for this local Windows use.
2>nul mkdir "%LOCK%"
if errorlevel 1 exit /b 0
> "%LOCK%\owner.txt" echo %DATE% %TIME% PID shell=%CMDCMDLINE%
>> "%LOG%" echo [%date% %time%] Watchdog started.

:loop
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 4 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo [%date% %time%] Health failed; requesting serialized restart. >> "%LOG%"
  call "start_stock_trader_background.cmd" >> "%LOG%" 2>&1
  timeout /t 20 /nobreak >nul
) else (
  > "%LOCK%\heartbeat.txt" echo %DATE% %TIME%
  timeout /t 60 /nobreak >nul
)
goto loop
