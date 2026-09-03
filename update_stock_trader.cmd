@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Stock Trader - Atomic Update v0.17.8

echo ======================================================
echo   NH Stock Trader - Atomic Update / Quiet Background
echo ======================================================
echo.
if not exist ".git" (echo [ERROR] Run this file from repository root.&pause&exit /b 1)
if not exist "server\.venv\Scripts\python.exe" (echo [ERROR] server\.venv Python not found.&pause&exit /b 1)
if not exist "server\atomic_update.py" (echo [ERROR] atomic_update.py not found. Run git pull once first.&pause&exit /b 1)

echo Updating from GitHub with preflight + health check + rollback...
"server\.venv\Scripts\python.exe" "server\atomic_update.py"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
 echo [OK] Stock Trader update/start completed.
 echo [INFO] Background health checks use Python only; recurring PowerShell is not used.
) else (
 echo [ERROR] Atomic update returned code %RC%.
 echo [INFO] Check %%TEMP%%\stock_trader_remote_update.log
)
pause
endlocal & exit /b %RC%
