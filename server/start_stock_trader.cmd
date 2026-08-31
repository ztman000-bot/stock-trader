@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Stock Trader Server v0.12.0

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv Python not found.
  exit /b 1
)

set "MASTER_PRESELECT=180"
set "FOCUS_SIZE=40"
set "MIN_MARKET_CAP_EOK=500"
set "MIN_TRADE_PRICE=1000"
set "MAX_SPREAD_PCT=0.25"
set "MIN_INTRADAY_RANGE_PCT=0.50"

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [INFO] Stock Trader is already listening on port 8000.
  exit /b 0
)

set "AUTO_BACKFILL=false"
echo Starting Stock Trader v0.12.0 fast boot - scanner 180 / focus 40...
start "Stock Trader Uvicorn" /b ".venv\Scripts\python.exe" -m uvicorn unified_app:app --host 0.0.0.0 --port 8000

for /L %%N in (1,1,30) do (
  powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1; if($r.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 goto :online
  timeout /t 1 /nobreak >nul
)

echo [ERROR] Server did not become healthy within 30 seconds.
exit /b 6

:online
echo [OK] Stock Trader API ONLINE.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/market/backfill' -TimeoutSec 300 | Out-Null } catch {}" >nul 2>&1
exit /b 0
