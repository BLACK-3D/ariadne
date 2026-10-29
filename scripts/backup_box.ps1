$ErrorActionPreference = 'Stop'
try {
    $backupRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    if ([IO.Path]::GetPathRoot($backupRoot) -ieq 'E:\') {
        throw 'Drive E: is excluded.'
    }
    function Assert-BackupPath([string]$Path) {
        $backupCandidate = [IO.Path]::GetFullPath($Path)
        while ($backupCandidate) {
            if (Test-Path -LiteralPath $backupCandidate) {
                $backupInfo = Get-Item -LiteralPath $backupCandidate -Force
                if (($backupInfo.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw 'Backup launcher paths must not contain links or reparse points.'
                }
            }
            $backupCandidate = [IO.Path]::GetDirectoryName($backupCandidate)
        }
    }
    Assert-BackupPath $backupRoot
    $backupPython = $null
    foreach ($backupRelative in @('runtime\python\python.exe', '.venv\Scripts\python.exe')) {
        $backupChoice = Join-Path $backupRoot $backupRelative
        Assert-BackupPath $backupChoice
        if (Test-Path -LiteralPath $backupChoice -PathType Leaf) {
            $backupPython = $backupChoice
            break
        }
    }
    if (-not $backupPython) {
        throw 'A local Python runtime is required. Provision this box first; nothing will be downloaded.'
    }
    $backupScript = Join-Path $backupRoot 'scripts\backup_box.py'
    Assert-BackupPath $backupScript
    Set-Location -LiteralPath $backupRoot
    # Explicit waiting also works with the bundled windowless-runtime settings.
    # Capture the small summary so the existing console remains the only window.
    $backupStart = New-Object System.Diagnostics.ProcessStartInfo
    $backupStart.FileName = $backupPython
    $backupStart.Arguments = '-I -B "' + $backupScript + '"'
    $backupStart.WorkingDirectory = $backupRoot
    $backupStart.UseShellExecute = $false
    $backupStart.CreateNoWindow = $true
    $backupStart.RedirectStandardOutput = $true
    $backupStart.RedirectStandardError = $true
    $backupProcess = New-Object System.Diagnostics.Process
    $backupProcess.StartInfo = $backupStart
    try {
        if (-not $backupProcess.Start()) { throw 'The local backup runtime did not start.' }
        $backupOutput = $backupProcess.StandardOutput.ReadToEndAsync()
        $backupErrors = $backupProcess.StandardError.ReadToEndAsync()
        $backupProcess.WaitForExit()
        if ($backupOutput.Result) { Write-Host $backupOutput.Result.TrimEnd() }
        if ($backupErrors.Result) { Write-Host $backupErrors.Result.TrimEnd() -ForegroundColor Red }
        $backupExit = $backupProcess.ExitCode
    } finally {
        $backupProcess.Dispose()
    }
    exit $backupExit
} catch {
    Write-Host 'Backup did not complete. No verified backup is claimed.' -ForegroundColor Red
    Write-Host $_.Exception.Message
    exit 1
}
