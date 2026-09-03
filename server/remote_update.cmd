@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0\.."
set "LOG=%TEMP%\stock_trader_remote_update.log"
set "FLAG=%TEMP%\stock_trader_update_in_progress.flag"
set "REQ_BEFORE="
set "REQ_AFTER="
set "OLD_HEAD="

echo update>"%FLAG%"
echo [%date% %time%] Remote update requested. >> "%LOG%"

git diff --quiet --
if errorlevel 1 goto :dirty
git diff --cached --quiet --
if errorlevel 1 goto :dirty
for /f "tokens=*" %%H in ('git rev-parse HEAD') do set "OLD_HEAD=%%H"
for /f "tokens=*" %%H in ('git hash-object server\requirements.txt 2^>nul') do set "REQ_BEFORE=%%H"

git pull --ff-only >> "%LOG%" 2>&1
if errorlevel 1 goto :pull_fail
for /f "tokens=*" %%H in ('git hash-object server\requirements.txt 2^>nul') do set "REQ_AFTER=%%H"

if not "!REQ_BEFORE!"=="!REQ_AFTER!" (
  "server\.venv\Scripts\python.exe" -m pip install -r "server\requirements.txt" --disable-pip-version-check >> "%LOG%" 2>&1
  if errorlevel 1 goto :preflight_fail
)

rem Validate syntax and core imports while the old server is still alive.
"server\.venv\Scripts\python.exe" "server\preflight.py" >> "%LOG%" 2>&1
if errorlevel 1 goto :preflight_fail

rem Only now stop the known-good listener.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do taskkill /PID %%P /F >nul 2>&1
for /L %%N in (1,1,15) do (
  netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
  if errorlevel 1 goto :port_free
  timeout /t 1 /nobreak >nul
)
echo [%date% %time%] ERROR: port 8000 did not release. >> "%LOG%"
goto :rollback

:port_free
call "server\start_stock_trader_background.cmd"
for /L %%N in (1,1,40) do (
  powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1; if($r.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 goto :online
  timeout /t 1 /nobreak >nul
)
echo [%date% %time%] ERROR: new server failed health; rolling back to !OLD_HEAD!. >> "%LOG%"
goto :rollback

:rollback
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do taskkill /PID %%P /F >nul 2>&1
if not "!OLD_HEAD!"=="" git reset --hard !OLD_HEAD! >> "%LOG%" 2>&1
set "STOCK_TRADER_SKIP_WATCHDOG=1"
call "server\start_stock_trader_background.cmd"
set "ROLLBACK_OK=0"
for /L %%N in (1,1,35) do (
  powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1; if($r.ok){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 (
    set "ROLLBACK_OK=1"
    goto :rollback_done
  )
  timeout /t 1 /nobreak >nul
)
:rollback_done
if "!ROLLBACK_OK!"=="1" (
  echo [%date% %time%] ROLLBACK OK: previous server restored. >> "%LOG%"
) else (
  echo [%date% %time%] CRITICAL: rollback server health failed. >> "%LOG%"
)
del /q "%FLAG%" >nul 2>&1
endlocal
exit /b 6

:online
echo [%date% %time%] Update applied; server health OK. >> "%LOG%"
del /q "%FLAG%" >nul 2>&1
endlocal
exit /b 0

:dirty
echo [%date% %time%] STOP: tracked local changes exist. >> "%LOG%"
del /q "%FLAG%" >nul 2>&1
endlocal
exit /b 2

:pull_fail
echo [%date% %time%] ERROR: git pull failed; old server remains running. >> "%LOG%"
del /q "%FLAG%" >nul 2>&1
endlocal
exit /b 3

:preflight_fail
echo [%date% %time%] ERROR: dependency/preflight failed; reverting code before server stop. >> "%LOG%"
if not "!OLD_HEAD!"=="" git reset --hard !OLD_HEAD! >> "%LOG%" 2>&1
del /q "%FLAG%" >nul 2>&1
endlocal
exit /b 4
