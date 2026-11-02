@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
set "ariadneLauncherExit=%ERRORLEVEL%"
if not "%ariadneLauncherExit%"=="0" (
  echo Ariadne could not start. Review the error above.
  echo If available, share only data\host\launcher-*.json, not the private log.
  pause
)
exit /b %ariadneLauncherExit%
