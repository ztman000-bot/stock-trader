@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" exit /b 1
set "LOCKDIR=%TEMP%\stock_trader_start.lock"
set "BOOTLOG=%TEMP%\stock_trader_bootstrap.log"

rem Fast-path: never launch a duplicate listener.
netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 exit /b 0

rem Atomic directory mutex. v0.11.1 could leave this directory behind if the
rem starter itself was killed during an update. Recover a stale lock only when
rem the port is still down after a short grace period.
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
echo [%date% %time%] Launching uvicorn. log=%RUNLOG% >> "%BOOTLOG%"
start "" /b ".venv\Scripts\python.exe" -m uvicorn unified_app:app --host 0.0.0.0 --port 8000 >> "%RUNLOG%" 2>&1

for /L %%N in (1,1,30) do (
  powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1; if($r.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 goto :online
  timeout /t 1 /nobreak >nul
)
echo [%date% %time%] ERROR: health timeout. See %RUNLOG% >> "%BOOTLOG%"
goto :done

:online
echo [%date% %time%] ONLINE. >> "%BOOTLOG%"
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/market/backfill' -TimeoutSec 300 | Out-Null } catch {}" >nul 2>&1

:done
2>nul rmdir "%LOCKDIR%"
endlocal
exit /b 0
