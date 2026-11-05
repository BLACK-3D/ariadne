@echo off
setlocal
for %%I in ("%~dp0..") do set "ariadneThetaRoot=%%~fI"
cd /d "%ariadneThetaRoot%"
if not exist "%ariadneThetaRoot%\runtime\python\python.exe" (
  echo Ariadne bundled Python is missing. Prepare Ariadne first.
  pause
  exit /b 1
)
"%ariadneThetaRoot%\runtime\python\python.exe" -I -B "%ariadneThetaRoot%\scripts\theta_terminal.py" setup
set "thetaSetupExit=%ERRORLEVEL%"
if "%thetaSetupExit%"=="0" echo Theta key protected locally. No subscription, download or Terminal launch occurred.
pause
exit /b %thetaSetupExit%
