@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\backup_box.ps1"
set "ARIADNE_BACKUP_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %ARIADNE_BACKUP_EXIT%
