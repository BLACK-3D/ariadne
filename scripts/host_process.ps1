param([ValidateSet('start','stop')][string]$Action, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskRoot 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Ariadne Python runtime is missing.' }
# Explicit process waiting also works in sparse/portable Windows environments.
# No shell quoting of paths or reliance on PowerShell's PATHEXT classification.
$taskInfo = [Diagnostics.ProcessStartInfo]::new()
$taskInfo.FileName = $taskPython
$taskInfo.WorkingDirectory = $taskRoot
$taskInfo.Arguments = '-I -B scripts/host.py ' + $Action
if ($Action -eq 'start' -and $NoBrowser) { $taskInfo.Arguments += ' --no-browser' }
$taskInfo.UseShellExecute = $false
$taskInfo.CreateNoWindow = $true
$taskInfo.RedirectStandardOutput = $true
$taskInfo.RedirectStandardError = $true
$taskProcess = [Diagnostics.Process]::Start($taskInfo)
try {
    $taskOutput = $taskProcess.StandardOutput.ReadToEndAsync()
    $taskError = $taskProcess.StandardError.ReadToEndAsync()
    if (-not $taskProcess.WaitForExit(145000)) { throw 'Ariadne launcher is still pending; inspect data/host/status.json. No process was force-killed.' }
    $taskProcess.WaitForExit()
    [Console]::Out.Write($taskOutput.Result)
    [Console]::Error.Write($taskError.Result)
    exit $taskProcess.ExitCode
} finally { $taskProcess.Dispose() }
