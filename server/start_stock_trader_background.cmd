@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [%date% %time%] ERROR: .venv Python not found >> stock_trader_startup.log
  exit /b 1
)

rem Do not start a duplicate server if port 8000 is already listening.
netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [%date% %time%] INFO: Stock Trader already listening on port 8000. >> stock_trader_startup.log
  exit /b 0
)

rem Fast boot: let the API/UI come online first. Historical 5m backfill starts after health is ready.
set "AUTO_BACKFILL=false"
echo [%date% %time%] Starting Stock Trader v0.9.x fast background boot... >> stock_trader_startup.log
start "" /b ".venv\Scripts\python.exe" -m uvicorn unified_app:app --host 0.0.0.0 --port 8000 >> stock_trader_server.log 2>&1

for /L %%N in (1,1,20) do (
  powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1; if($r.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 goto :online
  timeout /t 1 /nobreak >nul
)
echo [%date% %time%] WARN: fast boot health timeout. >> stock_trader_startup.log
exit /b 0

:online
echo [%date% %time%] Server ONLINE. Starting background NH 5m backfill... >> stock_trader_startup.log
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 1; try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/market/backfill' -TimeoutSec 120 | Out-Null } catch {}" >nul 2>&1
exit /b 0
