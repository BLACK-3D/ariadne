@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows.ps1" -Check
set "ariadneCheckExit=%ERRORLEVEL%"
pause
exit /b %ariadneCheckExit%
