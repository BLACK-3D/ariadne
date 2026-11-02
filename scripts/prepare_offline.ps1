$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$drive = [IO.Path]::GetPathRoot($taskRoot)
if ($drive -ieq 'E:\' -or $drive.StartsWith('\\')) { throw 'Use an allowed ordinary local drive.' }
foreach ($path in @($taskRoot,(Join-Path $taskRoot 'runtime\python\python.exe'),(Join-Path $taskRoot 'scripts\export_core.py'))) {
    $walk=$drive
    foreach ($part in ([IO.Path]::GetFullPath($path)).Substring($drive.Length).Split([IO.Path]::DirectorySeparatorChar)) {
        if (-not $part) { continue }; $walk=Join-Path $walk $part
        if (Test-Path -LiteralPath $walk) {
            $item=Get-Item -LiteralPath $walk -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or (-not $item.PSIsContainer -and $item.LinkType -eq 'HardLink')) { throw 'Offline-copy paths must not be links.' }
        }
    }
}
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) { throw 'Complete Setup Ariadne.cmd before preparing an offline copy.' }
Write-Host 'Preparing a clean offline core. This can need many gigabytes and several minutes.'
Write-Host 'Keep the resulting ZIP and checksum together. Private records and settings are excluded.'
& $taskPython -I -B -X utf8 (Join-Path $taskRoot 'scripts\export_core.py')
exit $LASTEXITCODE
