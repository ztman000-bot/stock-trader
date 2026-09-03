@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "LOG=%TEMP%\stock_trader_supervisor.log"
rem Legacy compatibility wrapper only. New install_autostart removes the old
rem every-minute supervisor task and uses one persistent pythonw watchdog.
".venv\Scripts\python.exe" health_probe.py runtime >nul 2>&1
if not errorlevel 1 exit /b 0
echo [%date% %time%] Legacy supervisor recovery requested. >> "%LOG%"
call "start_stock_trader_background.cmd" >> "%LOG%" 2>&1
timeout /t 5 /nobreak >nul
".venv\Scripts\python.exe" health_probe.py full >nul 2>&1
if errorlevel 1 (
 echo [%date% %time%] Recovery attempt did not become healthy. >> "%LOG%"
 exit /b 1
)
echo [%date% %time%] Recovery successful. >> "%LOG%"
exit /b 0
