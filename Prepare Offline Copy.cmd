@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\prepare_offline.ps1"
set "ariadneCopyExit=%ERRORLEVEL%"
pause
exit /b %ariadneCopyExit%
