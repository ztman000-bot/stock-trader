@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "LOG=%TEMP%\stock_trader_supervisor.log"

echo [%date% %time%] Supervisor check. >> "%LOG%"
powershell -NoProfile -Command "try { $h=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 4; $u=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/system/ui-health' -TimeoutSec 4; if($h.ok -and $u.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 exit /b 0

echo [%date% %time%] Health/UI check failed; starting recovery. >> "%LOG%"
call "start_stock_trader_background.cmd" >> "%LOG%" 2>&1

timeout /t 5 /nobreak >nul
powershell -NoProfile -Command "try { $h=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 4; $u=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/system/ui-health' -TimeoutSec 4; if($h.ok -and $u.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo [%date% %time%] Recovery attempt did not become healthy. >> "%LOG%"
  exit /b 1
)
echo [%date% %time%] Recovery successful. >> "%LOG%"
exit /b 0
