@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Stock Trader - One Click Update + Start v0.11.5

echo ======================================================
echo   NH Stock Trader - One Click Update + Start v0.11.5
echo ======================================================
echo.
where git >nul 2>&1
if errorlevel 1 (echo [ERROR] Git was not found in PATH.&pause&exit /b 1)
if not exist ".git" (echo [ERROR] Run this file from repository root.&pause&exit /b 1)
if not exist "server\.venv\Scripts\python.exe" (echo [ERROR] server\.venv Python not found.&pause&exit /b 1)

echo [1/6] Checking tracked local changes...
git diff --quiet --
if errorlevel 1 (echo [STOP] Tracked local changes exist.&git status --short&pause&exit /b 2)
git diff --cached --quiet --
if errorlevel 1 (echo [STOP] Staged local changes exist.&git status --short&pause&exit /b 2)

echo [2/6] Downloading latest code from GitHub...
git pull --ff-only
if errorlevel 1 (echo [ERROR] git pull failed.&pause&exit /b 3)

echo [3/6] Checking Python dependencies...
"server\.venv\Scripts\python.exe" -m pip install -r "server\requirements.txt" --disable-pip-version-check >nul
if errorlevel 1 (echo [ERROR] Dependency update failed.&pause&exit /b 4) else (echo Dependencies OK.)

echo [4/6] Checking Tailscale...
set "TAILSCALE_EXE="
where tailscale >nul 2>&1
if not errorlevel 1 set "TAILSCALE_EXE=tailscale"
if not defined TAILSCALE_EXE if exist "%ProgramFiles%\Tailscale\tailscale.exe" set "TAILSCALE_EXE=%ProgramFiles%\Tailscale\tailscale.exe"
set "TAILSCALE_IP="
if defined TAILSCALE_EXE for /f "usebackq delims=" %%I in (`"!TAILSCALE_EXE!" ip -4 2^>nul`) do if not defined TAILSCALE_IP set "TAILSCALE_IP=%%I"
if defined TAILSCALE_IP (echo Tailscale ONLINE: !TAILSCALE_IP!) else (echo [INFO] Tailscale IP not detected.)

echo [5/6] Stopping old server...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do taskkill /PID %%P /F >nul 2>&1
for /L %%N in (1,1,15) do (
  netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
  if errorlevel 1 goto :port_free
  timeout /t 1 /nobreak >nul
)
echo [ERROR] Port 8000 did not release.&pause&exit /b 5

:port_free
echo [6/6] Starting through canonical background launcher...
call "server\start_stock_trader_background.cmd"
set "SERVER_OK="
for /L %%N in (1,1,40) do (
  powershell -NoProfile -Command "try { $h=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2; $u=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/system/ui-health' -TimeoutSec 2; if($h.ok -and $u.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 (set "SERVER_OK=1"&goto :server_ready)
  timeout /t 1 /nobreak >nul
)
:server_ready
echo.
if defined SERVER_OK (
  echo STOCK TRADER ONLINE - API + UI HEALTH OK
  echo PC DayTrader: http://127.0.0.1:8000/classic
  if defined TAILSCALE_IP echo Phone DayTrader: http://!TAILSCALE_IP!:8000/classic
  start "" "http://127.0.0.1:8000/classic"
) else (
  echo [ERROR] Server/UI health check failed.
  echo [INFO] Check %%TEMP%%\stock_trader_bootstrap.log and latest stock_trader_server log.
  pause
  exit /b 6
)
endlocal
exit /b 0
