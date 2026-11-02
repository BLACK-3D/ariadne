@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows.ps1" %*
set "ariadneSetupExit=%ERRORLEVEL%"
if not "%ariadneSetupExit%"=="0" pause
exit /b %ariadneSetupExit%
