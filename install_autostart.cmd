@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Stock Trader - Install Autostart

echo ======================================================
echo   Stock Trader - Windows Autostart Installer
echo ======================================================
echo.

if not exist "server\.venv\Scripts\python.exe" (
  echo [ERROR] server\.venv\Scripts\python.exe not found.
  echo Run the normal setup first.
  pause
  exit /b 1
)

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS=%STARTUP%\StockTraderAutoStart.vbs"
set "OLDLNK=%STARTUP%\StockTraderAutoStart.lnk"

if exist "%OLDLNK%" del /q "%OLDLNK%" >nul 2>&1

> "%VBS%" echo Set sh = CreateObject("WScript.Shell")
>> "%VBS%" echo sh.Run Chr(34) ^& "%CD%\server\start_stock_trader_background.cmd" ^& Chr(34), 0, False

echo [OK] Background Stock Trader startup installed.
echo     %VBS%
echo.

echo [INFO] Checking Tailscale...
where tailscale >nul 2>&1
if not errorlevel 1 (
  tailscale status >nul 2>&1
  if not errorlevel 1 (
    echo [OK] Tailscale is installed and responding.
  ) else (
    echo [WARN] Tailscale is installed but not currently connected.
  )
) else (
  if exist "%ProgramFiles%\Tailscale\tailscale.exe" (
    "%ProgramFiles%\Tailscale\tailscale.exe" status >nul 2>&1
    if not errorlevel 1 (echo [OK] Tailscale is installed and responding.) else (echo [WARN] Tailscale is installed but not currently connected.)
  ) else (
    echo [WARN] Tailscale CLI not found. Install/login to Tailscale separately.
  )
)

echo.
echo [INFO] Starting Stock Trader in background now...
start "" wscript.exe "%VBS%"
timeout /t 5 /nobreak >nul

powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 4; Write-Host ('[OK] Server ONLINE - v' + $r.version) } catch { Write-Host '[WARN] Health check failed. Check server\stock_trader_server.log' }"

echo.
echo ======================================================
echo   INSTALL COMPLETE

echo   From next Windows login:
echo   - Tailscale: Windows app/service handles connection

echo   - Stock Trader: starts hidden in background

echo   - Browser: does NOT open automatically

echo   - Real NH orders remain controlled by server config

echo ======================================================
echo.
pause
endlocal
