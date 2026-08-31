@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Stock Trader - One Click Start / Update

echo ======================================================
echo   NH Stock Trader - One Click Update + Start
echo ======================================================
echo.

where git >nul 2>&1
if errorlevel 1 (echo [ERROR] Git was not found in PATH.&pause&exit /b 1)
if not exist ".git" (echo [ERROR] Run this file from the stock-trader repository root.&pause&exit /b 1)
if not exist "server\.venv\Scripts\python.exe" (echo [ERROR] server\.venv Python was not found.&pause&exit /b 1)

echo [1/6] Checking tracked local changes...
git diff --quiet --
if errorlevel 1 (echo [STOP] Tracked local changes exist. Update was NOT applied.&git status --short&pause&exit /b 2)
git diff --cached --quiet --
if errorlevel 1 (echo [STOP] Staged local changes exist. Update was NOT applied.&git status --short&pause&exit /b 2)

echo [2/6] Downloading latest code from GitHub...
git pull --ff-only
if errorlevel 1 (echo [ERROR] git pull failed. Existing local version was preserved.&pause&exit /b 3)

echo [3/6] Checking Python dependencies...
"server\.venv\Scripts\python.exe" -m pip install -r "server\requirements.txt" --disable-pip-version-check >nul
if errorlevel 1 (echo [WARNING] Dependency update failed. Existing packages will be used.) else (echo Dependencies OK.)

echo [4/6] Checking Tailscale...
set "TAILSCALE_EXE="
where tailscale >nul 2>&1
if not errorlevel 1 set "TAILSCALE_EXE=tailscale"
if not defined TAILSCALE_EXE if exist "%ProgramFiles%\Tailscale\tailscale.exe" set "TAILSCALE_EXE=%ProgramFiles%\Tailscale\tailscale.exe"
if not defined TAILSCALE_EXE if exist "%LocalAppData%\Tailscale\tailscale.exe" set "TAILSCALE_EXE=%LocalAppData%\Tailscale\tailscale.exe"
set "TAILSCALE_IP="
if defined TAILSCALE_EXE for /f "usebackq delims=" %%I in (`"!TAILSCALE_EXE!" ip -4 2^>nul`) do if not defined TAILSCALE_IP set "TAILSCALE_IP=%%I"
if defined TAILSCALE_IP (echo Tailscale ONLINE: !TAILSCALE_IP!) else (echo [INFO] Tailscale IP was not detected.)

echo [5/6] Stopping old Stock Trader server if running...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do taskkill /PID %%P /F >nul 2>&1
timeout /t 2 /nobreak >nul

echo [6/6] Starting Stock Trader server...
start "Stock Trader Server" /D "%CD%\server" cmd /k "start_stock_trader.cmd"
set "SERVER_OK="
for /L %%N in (1,1,20) do (
  powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2; if($r.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 (set "SERVER_OK=1"&goto :server_ready)
  timeout /t 1 /nobreak >nul
)
:server_ready
echo.
if defined SERVER_OK (
  echo STOCK TRADER ONLINE
  echo PC DayTrader: http://127.0.0.1:8000/classic
  if defined TAILSCALE_IP echo Phone DayTrader: http://!TAILSCALE_IP!:8000/classic
) else (
  echo [WARNING] Server health check did not respond yet.
)
if defined SERVER_OK start "" "http://127.0.0.1:8000/classic"
endlocal
exit /b 0
