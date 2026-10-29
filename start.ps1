param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& (Join-Path $PSScriptRoot 'scripts\host_process.ps1') -Action start -NoBrowser:$NoBrowser
