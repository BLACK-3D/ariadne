$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $taskRoot
& (Join-Path $PSScriptRoot 'host_process.ps1') -Action stop

$taskLauncherExit = $LASTEXITCODE
if ($null -eq $taskLauncherExit) { $taskLauncherExit = 0 }
exit $taskLauncherExit
