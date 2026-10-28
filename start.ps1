param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { $projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $projectPython)) { $projectPython = 'python' }
if ($NoBrowser) { & $projectPython -I -B scripts/launch.py --no-browser } else { & $projectPython -I -B scripts/launch.py }
