param([switch]$Check)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
function Assert-SetupPath([string]$TaskPath) {
    $taskFullPath = [IO.Path]::GetFullPath($TaskPath)
    $taskDriveRoot = [IO.Path]::GetPathRoot($taskFullPath)
    if ($taskDriveRoot -ieq 'E:\' -or $taskDriveRoot.StartsWith('\\')) { throw 'Use an allowed local drive; E: and network paths are excluded.' }
    $taskWalk = $taskDriveRoot
    # Check ancestors from the drive downward, before following a child path.
    foreach ($taskPart in $taskFullPath.Substring($taskDriveRoot.Length).Split([IO.Path]::DirectorySeparatorChar)) {
        if ($taskPart.Length -eq 0) { continue }
        $taskWalk = Join-Path $taskWalk $taskPart
        if (Test-Path -LiteralPath $taskWalk) {
            $taskItem = Get-Item -LiteralPath $taskWalk -Force
            if (($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Setup refuses links and junctions; use an ordinary local folder.' }
        }
    }
}
Assert-SetupPath $taskRoot
foreach ($taskRelative in @('.venv', '.venv\Scripts\python.exe', 'runtime', 'runtime\python\python.exe', 'runtime\tmp', 'runtime\wheels', 'runtime\pip-cache', 'data', 'data\host', 'data\server.token', 'data\ariadne.sqlite3', 'data\host\status.json')) {
    Assert-SetupPath (Join-Path $taskRoot $taskRelative)
}
Set-Location -LiteralPath $taskRoot
if (-not $Check) {
    foreach ($taskMarker in @('data\server.token', 'data\ariadne.sqlite3', 'data\host\status.json')) {
        if (Test-Path -LiteralPath (Join-Path $taskRoot $taskMarker)) {
            throw 'This folder already contains an Ariadne installation. Use Start Ariadne.cmd. Extract a fresh starter folder for a separate installation; setup will not modify this one.'
        }
    }
}
$taskPython = $null
$taskPrefix = @()
foreach ($taskCandidate in @('.venv\Scripts\python.exe', 'runtime\python\python.exe')) {
    $taskPath = Join-Path $taskRoot $taskCandidate
    if (Test-Path -LiteralPath $taskPath -PathType Leaf) { $taskPython = $taskPath; break }
}
if ($null -eq $taskPython) {
    $taskLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $taskLauncher) { $taskPython = $taskLauncher.Source; $taskPrefix = @('-3.13') }
}
if ($null -eq $taskPython) {
    $taskCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $taskCommand -and $taskCommand.Source -notlike '*\WindowsApps\*') { $taskPython = $taskCommand.Source }
}
if ($null -eq $taskPython) { throw 'Install Python 3.13 (64-bit) from https://www.python.org/downloads/windows/ and reopen setup. Read START_HERE.html.' }
$taskProbe = & $taskPython @taskPrefix -I -B -c 'import json,struct,sys,sysconfig; print(json.dumps(dict(version=list(sys.version_info[:2]),bits=struct.calcsize(chr(80))*8,platform=sysconfig.get_platform())))'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 could not start. Install the official 64-bit Python 3.13 runtime and retry.' }
$taskInfo = $taskProbe | ConvertFrom-Json
if ($taskInfo.version[0] -ne 3 -or $taskInfo.version[1] -ne 13 -or $taskInfo.bits -ne 64 -or $taskInfo.platform -ne 'win-amd64') { throw 'The Windows starter requires Python 3.13 (64-bit). Other platforms use the manual source guide.' }
if ($Check) {
    & $taskPython @taskPrefix -I -B scripts/setup_windows.py --check
    exit $LASTEXITCODE
}
# All setup caches and temporary files belong to this extracted starter.
foreach ($taskDirectory in @('runtime\tmp', 'runtime\wheels')) {
    $taskTarget = Join-Path $taskRoot $taskDirectory
    if (Test-Path -LiteralPath $taskTarget) {
        $taskItem = Get-Item -LiteralPath $taskTarget
        if (($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Setup refuses linked runtime folders.' }
    }
    New-Item -ItemType Directory -Force -Path $taskTarget | Out-Null
}
$env:TEMP = Join-Path $taskRoot 'runtime\tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $taskRoot 'runtime\pip-cache'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
function Invoke-SetupPython([string[]]$TaskArguments) {
    & $script:taskVenvPython @TaskArguments
    if ($LASTEXITCODE -ne 0) { throw 'Setup stopped after a failed step. Read its output above. No automatic retry or Ariadne launch was attempted.' }
}
Write-Host 'ARIADNE / FIRST-TIME LOCAL SETUP'
Write-Host 'This downloads pinned Python packages, the local model runtime and the 9B model. Keep Internet connected until complete.'
$taskVenvPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskVenvPython)) {
    & $taskPython @taskPrefix -I -B -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the project Python environment.' }
}
Invoke-SetupPython @('-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install', '--no-cache-dir', '--require-hashes', '-r', 'requirements-bootstrap.txt')
Invoke-SetupPython @('-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'download', '--no-cache-dir', '--only-binary=:all:', '--require-hashes', '--dest', 'runtime/wheels', '-r', 'requirements-bootstrap.txt', '-r', 'requirements-server.txt', '-r', 'requirements-documents.txt')
Invoke-SetupPython @('-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install', '--no-cache-dir', '--no-index', '--find-links', 'runtime/wheels', '--require-hashes', '-r', 'requirements-server.txt', '-r', 'requirements-documents.txt')
Invoke-SetupPython @('-I', '-B', 'scripts/setup_windows.py')
Write-Host ''
Write-Host 'Ready. Double-click Start Ariadne.cmd. Pair with your own key in data/server.token.'
Write-Host 'Local chat and installed tools work offline. No microphone or daily schedule has been enabled.'
Read-Host 'Press Enter to close setup' | Out-Null
