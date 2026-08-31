@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0\.."
set "LOG=%TEMP%\stock_trader_remote_update.log"
set "REQ_BEFORE="
set "REQ_AFTER="

echo [%date% %time%] Remote update requested. >> "%LOG%"

rem Ignore runtime-only untracked files. Block only tracked/staged local modifications.
git diff --quiet --
if errorlevel 1 (echo [%date% %time%] STOP: tracked local changes exist. >> "%LOG%"&exit /b 2)
git diff --cached --quiet --
if errorlevel 1 (echo [%date% %time%] STOP: staged local changes exist. >> "%LOG%"&exit /b 2)

for /f "tokens=*" %%H in ('git hash-object server\requirements.txt 2^>nul') do set "REQ_BEFORE=%%H"
git pull --ff-only >> "%LOG%" 2>&1
if errorlevel 1 (echo [%date% %time%] ERROR: git pull failed. >> "%LOG%"&exit /b 3)
for /f "tokens=*" %%H in ('git hash-object server\requirements.txt 2^>nul') do set "REQ_AFTER=%%H"

if not "!REQ_BEFORE!"=="!REQ_AFTER!" (
  echo [%date% %time%] requirements changed; installing dependencies. >> "%LOG%"
  "server\.venv\Scripts\python.exe" -m pip install -r "server\requirements.txt" --disable-pip-version-check >> "%LOG%" 2>&1
) else (
  echo [%date% %time%] requirements unchanged; skipping pip install. >> "%LOG%"
)

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do taskkill /PID %%P /F >nul 2>&1
timeout /t 1 /nobreak >nul

set "VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\StockTraderAutoStart.vbs"
if exist "%VBS%" (start "" wscript.exe "%VBS%") else (start "" /min cmd /c "server\start_stock_trader_background.cmd")
echo [%date% %time%] Update applied; restart requested. >> "%LOG%"
endlocal
