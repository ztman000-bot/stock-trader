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

rem Keep one independent watchdog alive. watchdog.py owns a Windows named mutex,
rem so calling this launcher repeatedly will not create duplicate watchdogs.
if not "%STOCK_TRADER_SKIP_WATCHDOG%"=="1" (
 start "" /b ".venv\Scripts\python.exe" watchdog.py >nul 2>&1
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
 powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1; if($r.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
 if not errorlevel 1 goto :online
 timeout /t 1 /nobreak >nul
)
echo [%date% %time%] ERROR: health timeout. See %RUNLOG% >> "%BOOTLOG%"
goto :done
:online
echo [%date% %time%] ONLINE localhost-only. scanner=180 focus=40 watchdog=enabled >> "%BOOTLOG%"
rem Startup backfill is research work. Never compete with live KRX REST traffic.
powershell -NoProfile -Command "$n=Get-Date; $m=$n.Hour*60+$n.Minute; if($n.DayOfWeek -notin @('Saturday','Sunday') -and $m -ge 540 -and $m -le 930){exit 0}; try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/market/backfill' -TimeoutSec 300 | Out-Null } catch {}" >nul 2>&1
:done
2>nul rmdir "%LOCKDIR%"
endlocal
exit /b 0
