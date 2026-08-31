@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0\.."
set "LOG=%TEMP%\stock_trader_remote_update.log"
set "REQ_BEFORE="
set "REQ_AFTER="

echo [%date% %time%] Remote update requested. >> "%LOG%"

git diff --quiet --
if errorlevel 1 (echo [%date% %time%] STOP: tracked local changes exist. >> "%LOG%"&exit /b 2)
git diff --cached --quiet --
if errorlevel 1 (echo [%date% %time%] STOP: staged local changes exist. >> "%LOG%"&exit /b 2)

for /f "tokens=*" %%H in ('git hash-object server\requirements.txt 2^>nul') do set "REQ_BEFORE=%%H"
git pull --ff-only >> "%LOG%" 2>&1
if errorlevel 1 (echo [%date% %time%] ERROR: git pull failed. >> "%LOG%"&exit /b 3)
for /f "tokens=*" %%H in ('git hash-object server\requirements.txt 2^>nul') do set "REQ_AFTER=%%H"

if not "!REQ_BEFORE!"=="!REQ_AFTER!" (
  "server\.venv\Scripts\python.exe" -m pip install -r "server\requirements.txt" --disable-pip-version-check >> "%LOG%" 2>&1
  if errorlevel 1 (echo [%date% %time%] ERROR: dependency install failed. >> "%LOG%"&exit /b 4)
)

rem Stop every listener currently owning port 8000.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do taskkill /PID %%P /F >nul 2>&1

rem Do not guess that one second is enough. Wait until Windows confirms port release.
for /L %%N in (1,1,15) do (
  netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
  if errorlevel 1 goto :port_free
  timeout /t 1 /nobreak >nul
)
echo [%date% %time%] ERROR: port 8000 did not release. >> "%LOG%"
exit /b 5

:port_free
rem Use exactly one race-safe restart path; do not invoke Startup VBS here.
call "server\start_stock_trader_background.cmd"

for /L %%N in (1,1,35) do (
  powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1; if($r.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 goto :online
  timeout /t 1 /nobreak >nul
)
echo [%date% %time%] ERROR: restarted server failed health check. >> "%LOG%"
exit /b 6

:online
echo [%date% %time%] Update applied; server health OK. >> "%LOG%"
endlocal
exit /b 0
