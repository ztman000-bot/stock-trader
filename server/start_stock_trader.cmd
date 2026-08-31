@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Stock Trader Server v0.11.2

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv Python not found.
  exit /b 1
)

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [INFO] Stock Trader is already listening on port 8000.
  exit /b 0
)

rem Critical stability rule: API health must never wait for 120-code historical backfill.
rem Bring FastAPI online first; request backfill only after /api/health is reachable.
set "AUTO_BACKFILL=false"
echo Starting Stock Trader v0.11.2 fast boot...
start "Stock Trader Uvicorn" /b ".venv\Scripts\python.exe" -m uvicorn unified_app:app --host 0.0.0.0 --port 8000

for /L %%N in (1,1,30) do (
  powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1; if($r.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 goto :online
  timeout /t 1 /nobreak >nul
)

echo [ERROR] Server did not become healthy within 30 seconds.
echo [INFO] Run: .venv\Scripts\python.exe -m uvicorn unified_app:app --host 0.0.0.0 --port 8000
exit /b 6

:online
echo [OK] Stock Trader API ONLINE.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/market/backfill' -TimeoutSec 300 | Out-Null } catch {}" >nul 2>&1
exit /b 0
