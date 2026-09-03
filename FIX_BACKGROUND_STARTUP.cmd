@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "server\fix_background_startup.cmd" (
  echo [ERROR] server\fix_background_startup.cmd not found.
  echo Run server\remote_update.cmd first, then try again.
  pause
  exit /b 10
)
call "server\fix_background_startup.cmd"
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
