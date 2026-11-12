@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\python\python.exe" (
  echo Run Setup Ariadne.cmd in this folder first to prepare its private Python.
  pause
  exit /b 1
)
echo Preparing the optional local CPU vision pack inside this Ariadne folder.
echo This explicit setup downloads pinned official assets. It does not open a camera.
if not exist "runtime\vision\ffmpeg\ffmpeg.exe" (
  "runtime\python\python.exe" -I -B "scripts\build_vision_decoder.py" --download
  if errorlevel 1 goto failed
)
"runtime\python\python.exe" -I -B "scripts\provision_vision.py" --download
if errorlevel 1 goto failed
echo Vision pack prepared. Select a video or explicitly start a camera in Ariadne.
pause
exit /b 0
:failed
echo Vision setup stopped. Preserve the error and inspect the named partial assets.
pause
exit /b 1
