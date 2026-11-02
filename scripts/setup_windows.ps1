param([switch]$Check, [switch]$NoPause)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
try { [Console]::OutputEncoding = $OutputEncoding } catch { }
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
function Assert-SetupPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $drive = [IO.Path]::GetPathRoot($full)
    if ($drive -ieq 'E:\' -or $drive.StartsWith('\\')) { throw 'Use an ordinary allowed local drive; E: and network paths are excluded.' }
    $walk = $drive
    foreach ($part in $full.Substring($drive.Length).Split([IO.Path]::DirectorySeparatorChar)) {
        if (-not $part) { continue }
        $walk = Join-Path $walk $part
        if (Test-Path -LiteralPath $walk) {
            $item = Get-Item -LiteralPath $walk -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or (-not $item.PSIsContainer -and $item.LinkType -eq 'HardLink')) { throw 'Setup refuses links and junctions.' }
        }
    }
}
Assert-SetupPath $taskRoot
Set-Location -LiteralPath $taskRoot
foreach ($name in @('runtime','runtime\python','runtime\python\python.exe','scripts\setup_windows.py','scripts\bootstrap_python.ps1','runtime\tmp','runtime\wheels','data','data\host')) { Assert-SetupPath (Join-Path $taskRoot $name) }
$taskPython = Join-Path $taskRoot 'runtime\python\python.exe'
if ($Check) {
    if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) {
        [Console]::Out.WriteLine('{"schema":1,"mode":"check","status":"runtime_missing","python_mode":"missing","downloads":false,"services_started":false}')
        exit 1
    }
    & $taskPython -I -B -X utf8 (Join-Path $taskRoot 'scripts\setup_windows.py') --check
    exit $LASTEXITCODE
}
foreach ($marker in @('data\server.token','data\ariadne.sqlite3','data\host\status.json')) {
    Assert-SetupPath (Join-Path $taskRoot $marker)
    if (Test-Path -LiteralPath (Join-Path $taskRoot $marker)) { throw 'This is an existing private installation. Use Start Ariadne.cmd. Setup will not change it.' }
}
if (Test-Path -LiteralPath (Join-Path $taskRoot 'runtime\SETUP-READY.json')) { Write-Host 'Setup already completed. Use Start Ariadne.cmd.'; exit 0 }
if (Test-Path -LiteralPath (Join-Path $taskRoot 'runtime\SETUP-FAILED.json')) { throw 'A previous setup failed. Inspect the error and cached files before retrying; use a fresh starter folder for another attempt.' }
if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'The guided starter requires native Windows x64. ARM64 and other systems use the manual guide.' }
Write-Host 'ARIADNE / SELF-CONTAINED WINDOWS SETUP'
Write-Host 'No installed Python, Git, Docker or Node is required. Initial Internet downloads prepare this folder only.'
$taskStage = 'python_bootstrap'
try {
    $taskShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    & $taskShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $taskRoot 'scripts\bootstrap_python.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Private Python bootstrap failed. Inspect the error above; no automatic retry.' }
    $taskStage = 'python_qualification'
    $probe = & $taskPython -I -B -X utf8 -c 'import json,sys,sysconfig,struct,ssl,sqlite3;print(json.dumps(dict(executable=sys.executable,prefix=sys.prefix,base_prefix=sys.base_prefix,version=list(sys.version_info[:2]),bits=struct.calcsize(chr(80))*8,platform=sysconfig.get_platform())))'
    if ($LASTEXITCODE -ne 0) { throw 'The private Python runtime could not load SSL/sqlite.' }
    $identity = $probe | ConvertFrom-Json
    $expected = [IO.Path]::GetFullPath((Join-Path $taskRoot 'runtime\python'))
    if ($identity.prefix -ine $expected -or $identity.base_prefix -ine $expected -or $identity.executable -ine $taskPython -or $identity.version[0] -ne 3 -or $identity.version[1] -ne 13 -or $identity.bits -ne 64 -or $identity.platform -ne 'win-amd64') { throw 'Private Python resolved outside this folder or failed its platform check.' }
    foreach ($directory in @('runtime\tmp','runtime\wheels')) { Assert-SetupPath (Join-Path $taskRoot $directory); [void][IO.Directory]::CreateDirectory((Join-Path $taskRoot $directory)) }
    $env:TEMP = Join-Path $taskRoot 'runtime\tmp'
    $env:TMP = $env:TEMP
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    function Invoke-SetupPython([string[]]$Arguments) {
        & $script:taskPython @Arguments
        if ($LASTEXITCODE -ne 0) { throw 'Setup stopped after the failed step above; no automatic retry or application start.' }
    }
    function Test-CoreWheelCache {
        $complete = $true
        foreach ($requirement in @('requirements-bootstrap.txt','requirements-server.txt','requirements-documents.txt')) {
            foreach ($line in [IO.File]::ReadAllLines((Join-Path $taskRoot $requirement))) {
                if (-not $line.Trim() -or $line.TrimStart().StartsWith('#')) { continue }
                $match = [regex]::Match($line,'^([A-Za-z0-9_-]+)==([A-Za-z0-9_.+-]+)\s+--hash=sha256:([a-f0-9]{64})\s*$')
                if (-not $match.Success) { throw 'Unsupported core wheel requirement.' }
                $pattern = $match.Groups[1].Value.Replace('-','_') + '-' + $match.Groups[2].Value + '-*.whl'
                $candidates = @(Get-ChildItem -LiteralPath (Join-Path $taskRoot 'runtime\wheels') -Filter $pattern -File)
                if ($candidates.Count -eq 0) { $complete=$false; continue }
                if ($candidates.Count -ne 1) { throw 'Conflicting core wheel cache entries.' }
                Assert-SetupPath $candidates[0].FullName
                if ((Get-FileHash -LiteralPath $candidates[0].FullName -Algorithm SHA256).Hash -ine $match.Groups[3].Value) { throw 'A cached core wheel failed its checksum. Inspect it; setup will not replace it automatically.' }
            }
        }
        return $complete
    }
    $taskStage = 'core_dependencies'
    if (Test-CoreWheelCache) { Write-Host 'All core wheels already match their pins; no package-index request is needed.' }
    else {
        Invoke-SetupPython @('-I','-B','-X','utf8','-m','pip','--isolated','--disable-pip-version-check','download','--no-cache-dir','--only-binary=:all:','--require-hashes','--dest','runtime/wheels','-r','requirements-bootstrap.txt','-r','requirements-server.txt','-r','requirements-documents.txt')
    }
    Invoke-SetupPython @('-I','-B','-X','utf8','-m','pip','--isolated','--disable-pip-version-check','install','--no-cache-dir','--no-index','--find-links','runtime/wheels','--require-hashes','-r','requirements-bootstrap.txt','-r','requirements-server.txt','-r','requirements-documents.txt')
    $taskStage = 'runtime_seal'
    Invoke-SetupPython @('-I','-B','-X','utf8','scripts/setup_windows.py','--seal-runtime')
    $taskStage = 'model_provision'
    Invoke-SetupPython @('-I','-B','-X','utf8','scripts/setup_windows.py')
    Write-Host ''
    Write-Host 'Ready. Double-click Start Ariadne.cmd. Pair with the private key in data/server.token after first start.'
    Write-Host 'This folder now contains its own Python, local model and core tools. Use Prepare Offline Copy.cmd for a clean offline handoff.'
    Write-Host 'No microphone, recurring schedule or Windows startup task has been enabled.'
    if (-not $NoPause) { Read-Host 'Press Enter to close setup' | Out-Null }
    exit 0
} catch {
    $taskOriginalError = $_
    # Constant structural summary only; the console may contain private paths.
    $failure = [ordered]@{schema=1;status='failed';stage=$taskStage;error_type='SetupError';automatic_retry=$false}
    try {
        $path = Join-Path $taskRoot 'runtime\SETUP-FAILED.json'
        Assert-SetupPath $path
        if (Test-Path -LiteralPath (Split-Path -Parent $path)) {
            $stream = [IO.FileStream]::new($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
            try { $writer=[IO.StreamWriter]::new($stream,[Text.UTF8Encoding]::new($false));$writer.WriteLine(($failure | ConvertTo-Json));$writer.Flush() } finally { $stream.Dispose() }
        }
    } catch { [Console]::Error.WriteLine('Setup failure summary could not be saved. Keep the error window for diagnosis.') }
    $taskDetail = [string]$taskOriginalError.Exception.Message
    if ($taskDetail.Length -gt 500) { $taskDetail = $taskDetail.Substring(0,500) }
    [Console]::Error.WriteLine($taskDetail)
    [Console]::Error.WriteLine('Ariadne setup stopped at ' + $taskStage + '. Review the preceding error; no automatic retry or launch occurred.')
    exit 1
}
