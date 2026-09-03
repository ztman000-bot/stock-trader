@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" exit /b 1
set "LOCKDIR=%TEMP%\stock_trader_start.lock"
set "BOOTLOG=%TEMP%\stock_trader_bootstrap.log"
set "MASTER_PRESELECT=180"
set "FOCUS_SIZE=40"
set "MIN_MARKET_CAP_EOK=500"
set "MIN_TRADE_PRICE=1000"
set "MAX_SPREAD_PCT=0.25"
set "MIN_INTRADAY_RANGE_PCT=0.50"

rem One Python watchdog only. pythonw has no console window and the watchdog owns
rem a Windows named mutex, so repeated launcher calls cannot create duplicates.
if not "%STOCK_TRADER_SKIP_WATCHDOG%"=="1" (
 if exist ".venv\Scripts\pythonw.exe" start "" /b ".venv\Scripts\pythonw.exe" watchdog.py >nul 2>&1
)

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 exit /b 0
2>nul mkdir "%LOCKDIR%"
if errorlevel 1 (
 timeout /t 4 /nobreak >nul
 netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
 if not errorlevel 1 exit /b 0
 2>nul rmdir "%LOCKDIR%"
 2>nul mkdir "%LOCKDIR%"
 if errorlevel 1 exit /b 0
)
set "AUTO_BACKFILL=false"
set "RUNLOG=%TEMP%\stock_trader_server_%RANDOM%_%RANDOM%.log"
echo [%date% %time%] Launching localhost-only uvicorn. log=%RUNLOG% >> "%BOOTLOG%"
start "" /b ".venv\Scripts\python.exe" -m uvicorn unified_app:app --host 127.0.0.1 --port 8000 >> "%RUNLOG%" 2>&1
for /L %%N in (1,1,30) do (
 ".venv\Scripts\python.exe" health_probe.py health >nul 2>&1
 if not errorlevel 1 goto :online
 timeout /t 1 /nobreak >nul
)
echo [%date% %time%] ERROR: health timeout. See %RUNLOG% >> "%BOOTLOG%"
goto :done
:online
echo [%date% %time%] ONLINE localhost-only. scanner=180 focus=40 watchdog=pythonw >> "%BOOTLOG%"
rem Best-effort after-hours research backfill. No PowerShell process is spawned.
".venv\Scripts\python.exe" health_probe.py backfill-if-safe >nul 2>&1
:done
2>nul rmdir "%LOCKDIR%"
endlocal
exit /b 0
