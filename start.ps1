param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$headlessFlag = [Environment]::GetEnvironmentVariable('ARIADNE_NO_BROWSER', 'Process')
if ($null -ne $headlessFlag -and $headlessFlag -notin @('0', '1')) {
    throw 'ARIADNE_NO_BROWSER must be 0 or 1.'
}
if ($headlessFlag -eq '1') { $NoBrowser = $true }
Set-Location -LiteralPath $PSScriptRoot
& (Join-Path $PSScriptRoot 'scripts\host_process.ps1') -Action start -NoBrowser:$NoBrowser
