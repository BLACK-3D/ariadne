@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_host.ps1"
if errorlevel 1 pause
