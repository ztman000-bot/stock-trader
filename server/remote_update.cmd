@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
set "LOG=server\remote_update.log"

echo [%date% %time%] Remote update requested. >> "%LOG%"
timeout /t 2 /nobreak >nul

git status --porcelain > "%TEMP%\stock_trader_remote_status.txt"
for %%A in ("%TEMP%\stock_trader_remote_status.txt") do if %%~zA GTR 0 (
  echo [%date% %time%] STOP: local changes exist. >> "%LOG%"
  del "%TEMP%\stock_trader_remote_status.txt" >nul 2>&1
  exit /b 2
)
del "%TEMP%\stock_trader_remote_status.txt" >nul 2>&1

git pull --ff-only >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] ERROR: git pull failed. >> "%LOG%"
  exit /b 3
)

"server\.venv\Scripts\python.exe" -m pip install -r "server\requirements.txt" --disable-pip-version-check >> "%LOG%" 2>&1

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do taskkill /PID %%P /F >nul 2>&1
timeout /t 2 /nobreak >nul

start "" wscript.exe "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\StockTraderAutoStart.vbs"
echo [%date% %time%] Update applied; restart requested. >> "%LOG%"
endlocal
