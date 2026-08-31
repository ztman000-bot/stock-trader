@echo off
setlocal EnableExtensions
chcp 65001 >nul

cd /d "%~dp0"
title Stock Trader - One Click Update

echo ======================================================
echo   Stock Trader - GitHub Update + Server Restart
echo ======================================================
echo.

where git >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Git was not found in PATH.
  echo Please install Git for Windows or fix PATH.
  pause
  exit /b 1
)

if not exist ".git" (
  echo [ERROR] This file must be run from the stock-trader repository root.
  echo Current folder: %CD%
  pause
  exit /b 1
)

echo [1/4] Checking local changes...
git status --porcelain > "%TEMP%\stock_trader_git_status.txt"
for %%A in ("%TEMP%\stock_trader_git_status.txt") do if %%~zA GTR 0 (
  echo.
  echo [STOP] Local changes exist. Update was NOT applied to protect your files.
  git status --short
  echo.
  echo Commit/stash the changes first, then run this updater again.
  del "%TEMP%\stock_trader_git_status.txt" >nul 2>&1
  pause
  exit /b 2
)
del "%TEMP%\stock_trader_git_status.txt" >nul 2>&1

echo [2/4] Downloading latest code from GitHub...
git pull --ff-only
if errorlevel 1 (
  echo.
  echo [ERROR] git pull failed. Existing local version was preserved.
  pause
  exit /b 3
)

echo [3/4] Stopping old local server if running...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do (
  echo Stopping PID %%P on port 8000...
  taskkill /PID %%P /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo [4/4] Starting updated Stock Trader server...
if not exist "server\start_stock_trader.cmd" (
  echo [ERROR] server\start_stock_trader.cmd was not found.
  pause
  exit /b 4
)
start "Stock Trader Server" /D "%CD%\server" cmd /k "start_stock_trader.cmd"

echo.
echo ======================================================
echo   UPDATE COMPLETE

echo   Health check: http://127.0.0.1:8000/api/health
echo ======================================================
echo.
echo Waiting a few seconds for server startup...
timeout /t 7 /nobreak >nul
start "" "http://127.0.0.1:8000/api/health"

endlocal
exit /b 0
