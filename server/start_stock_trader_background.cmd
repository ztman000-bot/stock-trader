@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" exit /b 1

rem Cross-process start mutex. Prevent watchdog/update/autostart from racing.
set "LOCKDIR=%TEMP%\stock_trader_start.lock"
2>nul mkdir "%LOCKDIR%"
if errorlevel 1 exit /b 0

rem Always release the mutex through :done.
netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 goto :done

rem Fast boot. Use a per-launch log file so stale processes can never lock the
rem next launch's stdout/stderr target.
set "AUTO_BACKFILL=false"
set "RUNLOG=%TEMP%\stock_trader_server_%RANDOM%_%RANDOM%.log"
start "" /b ".venv\Scripts\python.exe" -m uvicorn unified_app:app --host 0.0.0.0 --port 8000 >> "%RUNLOG%" 2>&1

for /L %%N in (1,1,30) do (
  powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1; if($r.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 goto :online
  timeout /t 1 /nobreak >nul
)
goto :done

:online
rem Backfill only after API health is confirmed.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 1; try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/market/backfill' -TimeoutSec 180 | Out-Null } catch {}" >nul 2>&1

:done
2>nul rmdir "%LOCKDIR%"
endlocal
exit /b 0
