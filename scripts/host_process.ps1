param([ValidateSet('start','stop')][string]$Action, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskHandles = [Collections.Generic.List[IntPtr]]::new()
$taskPinnedDirectories = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$taskUnsafePath = $false
$taskLog = $null
$taskLogName = $null
$taskReceiptName = $null
$taskProcess = $null
$taskStage = 'path_validation'
$taskExitCode = 1
$taskStatus = 'failed'
$taskFailure = 'Error'
$taskInterpreter = 'missing'
$taskOutputText = ''
$taskErrorText = ''
$taskOutputTruncated = $false
$taskErrorTruncated = $false
$taskStdout = $null
$taskStderr = $null
$taskFailureNote = ''
$taskConsoleStreamsWritten = $false
$taskAttempt = [Guid]::NewGuid().ToString('N')
$taskLimit = 16384

function New-LauncherNative {
    # Reflection.Emit keeps native declarations in memory. Add-Type on Windows
    # PowerShell 5.1 would create compiler artifacts outside a fresh box.
    $assembly = [AppDomain]::CurrentDomain.DefineDynamicAssembly([Reflection.AssemblyName]::new('AriadneLauncher' + $taskAttempt), [Reflection.Emit.AssemblyBuilderAccess]::Run)
    $module = $assembly.DefineDynamicModule('Native')
    $builder = $module.DefineType('Native', [Reflection.TypeAttributes]::Public -bor [Reflection.TypeAttributes]::Abstract -bor [Reflection.TypeAttributes]::Sealed)
    $attributes = [Reflection.MethodAttributes]::Public -bor [Reflection.MethodAttributes]::Static -bor [Reflection.MethodAttributes]::PinvokeImpl
    $definitions = @(
        @('CreateFileW', [IntPtr], [type[]]@([string],[uint32],[uint32],[IntPtr],[uint32],[uint32],[IntPtr])),
        @('GetFileInformationByHandle', [bool], [type[]]@([IntPtr],[IntPtr])),
        @('CloseHandle', [bool], [type[]]@([IntPtr]))
    )
    foreach ($definition in $definitions) {
        $method = $builder.DefinePInvokeMethod($definition[0], 'kernel32.dll', $definition[0], $attributes, [Reflection.CallingConventions]::Standard, $definition[1], $definition[2], [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
    }
    return $builder.CreateType()
}

function Reject-LauncherPath {
    $script:taskUnsafePath = $true
    throw [IO.IOException]::new('Launcher paths must be ordinary local files and folders without links.')
}

function Get-LauncherAttributes([string]$Path) {
    try { return [int][IO.File]::GetAttributes($Path) }
    catch [IO.FileNotFoundException] { return -1 }
    catch [IO.DirectoryNotFoundException] { return -1 }
}

function Assert-LauncherHandle([IntPtr]$Handle, [bool]$Directory) {
    $buffer = [Runtime.InteropServices.Marshal]::AllocHGlobal(52)
    try {
        if (-not $script:taskNative.GetMethod('GetFileInformationByHandle').Invoke($null, [object[]]@($Handle,$buffer))) { throw [IO.IOException]::new('Could not verify a launcher handle.') }
        $attributes = [Runtime.InteropServices.Marshal]::ReadInt32($buffer, 0)
        $links = [Runtime.InteropServices.Marshal]::ReadInt32($buffer, 40)
        if (($attributes -band 0x400) -ne 0 -or (($attributes -band 0x10) -ne 0) -ne $Directory -or (-not $Directory -and $links -ne 1)) { Reject-LauncherPath }
    } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($buffer) }
}

function Pin-LauncherDirectory([string]$Path, [bool]$Create = $false) {
    $full = [IO.Path]::GetFullPath($Path)
    $drive = [IO.Path]::GetPathRoot($full)
    if ($drive -ieq 'E:\' -or $drive.StartsWith('\\')) { Reject-LauncherPath }
    $parts = [Collections.Generic.List[string]]::new()
    $parts.Add($drive)
    $walk = $drive
    foreach ($part in $full.Substring($drive.Length).Split([IO.Path]::DirectorySeparatorChar)) {
        if ($part.Length -eq 0) { continue }
        $walk = Join-Path $walk $part
        $parts.Add($walk)
    }
    foreach ($component in $parts) {
        if ($script:taskPinnedDirectories.Contains($component)) { continue }
        $attributes = Get-LauncherAttributes $component
        if ($attributes -eq -1) {
            if (-not $Create) { return $false }
            # The preceding directory is already pinned against replacement.
            [void][IO.Directory]::CreateDirectory($component)
        } elseif (($attributes -band 0x400) -ne 0 -or ($attributes -band 0x10) -eq 0) { Reject-LauncherPath }
        # FILE_LIST_DIRECTORY and no FILE_SHARE_DELETE pin every ancestor.
        # OPEN_REPARSE_POINT verifies the final component without following it.
        $handle = $script:taskNative.GetMethod('CreateFileW').Invoke($null, [object[]]@($component,[uint32]1,[uint32]3,[IntPtr]::Zero,[uint32]3,[uint32]0x02200000,[IntPtr]::Zero))
        if ($handle -eq [IntPtr]::new(-1)) { throw [IO.IOException]::new('Could not pin a launcher directory.') }
        $script:taskHandles.Add($handle)
        Assert-LauncherHandle $handle $true
        [void]$script:taskPinnedDirectories.Add($component)
    }
    return $true
}

function Pin-LauncherFile([string]$Path) {
    if (-not (Pin-LauncherDirectory ([IO.Path]::GetDirectoryName($Path)))) { return $false }
    $attributes = Get-LauncherAttributes $Path
    if ($attributes -eq -1) { return $false }
    if (($attributes -band 0x400) -ne 0 -or ($attributes -band 0x10) -ne 0) { Reject-LauncherPath }
    $handle = $script:taskNative.GetMethod('CreateFileW').Invoke($null, [object[]]@($Path,[uint32]2147483648,[uint32]1,[IntPtr]::Zero,[uint32]3,[uint32]0x00200000,[IntPtr]::Zero))
    if ($handle -eq [IntPtr]::new(-1)) { throw [IO.IOException]::new('Could not pin a launcher file.') }
    $script:taskHandles.Add($handle)
    Assert-LauncherHandle $handle $false
    return $true
}

function Protect-LauncherText([string]$Text) {
    if ($null -eq $Text) { return '' }
    $text = [regex]::Replace($Text, '[\x00-\x08\x0b\x0c\x0e-\x1f]', '')
    $text = [regex]::Replace($text, '(?i)(authorization\s*:\s*bearer\s+)\S+', '$1[redacted]')
    $text = [regex]::Replace($text, '(?i)((?:api[_-]?key|access[_-]?token|password|passwd|secret)\s*[=:]\s*)[^\s,;]+', '$1[redacted]')
    return $text
}

function Read-LauncherStream($State) {
    if ($State.Ended -or -not $State.Pending.IsCompleted) { return }
    # ReadAsync itself executes in .NET, never a PowerShell background callback.
    # Both pipe reads stay outstanding concurrently, avoiding a full-pipe deadlock.
    $count = $State.Pending.GetAwaiter().GetResult()
    $State.Total += [long]$count
    if ($count -eq 0) { $State.Ended = $true; $State.Pending = $null; return }
    $retain = [Math]::Min($count, $script:taskLimit - $State.Kept.Length)
    if ($retain -gt 0) { [void]$State.Kept.Append($State.Buffer,0,$retain) }
    $State.Pending = $State.Reader.ReadAsync($State.Buffer,0,$State.Buffer.Length)
}

function Get-LauncherFailureType($ErrorRecord) {
    if ($script:taskUnsafePath) { return 'UnsafePath' }
    $name = $ErrorRecord.Exception.GetType().Name
    if ($name -in @('IOException','UnauthorizedAccessException','FileNotFoundException','DirectoryNotFoundException','Win32Exception','TimeoutException','ArgumentException','InvalidOperationException')) { return $name }
    return 'Error'
}

try {
    $taskNative = New-LauncherNative
    if (-not (Pin-LauncherDirectory $taskRoot)) { throw [IO.DirectoryNotFoundException]::new('Ariadne folder is missing.') }
    $taskStage = 'diagnostic'
    try {
        $taskLogDirectory = Join-Path $taskRoot 'data\host'
        if (Pin-LauncherDirectory $taskLogDirectory $true) {
            $taskLogName = 'launcher-' + $taskAttempt + '.log'
            $taskReceiptName = 'launcher-' + $taskAttempt + '.json'
            $taskLogPath = Join-Path $taskLogDirectory $taskLogName
            $taskLogStream = [IO.FileStream]::new($taskLogPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
            $taskLog = [IO.StreamWriter]::new($taskLogStream,[Text.UTF8Encoding]::new($false))
            $taskLog.WriteLine('PRIVATE Ariadne launcher diagnostic. May contain local paths. Share the adjacent JSON summary instead.')
            $taskLog.Flush()
            $taskLogStream.Flush($true)
        }
    } catch {
        if ($taskUnsafePath) { throw }
        [Console]::Error.WriteLine('Ariadne launcher: private diagnostic storage is unavailable; console output and the original exit code will still be retained.')
    }
    $taskStage = 'runtime'
    $taskPython = Join-Path $taskRoot 'runtime\python\python.exe'
    if (Pin-LauncherFile $taskPython) { $taskInterpreter = 'bundled' }
    else {
        $taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
        if (Pin-LauncherFile $taskPython) { $taskInterpreter = 'venv' }
        else { $taskFailure = 'RuntimeMissing'; throw [IO.FileNotFoundException]::new('Ariadne Python runtime is missing. Complete Setup Ariadne.cmd first.') }
    }
    $taskStage = 'source'
    if (-not (Pin-LauncherFile (Join-Path $taskRoot 'scripts\host.py'))) { $taskFailure = 'SourceMissing'; throw [IO.FileNotFoundException]::new('Ariadne host source is missing. Extract the complete starter folder.') }
    $taskStage = 'python_start'
    $taskInfo = [Diagnostics.ProcessStartInfo]::new()
    $taskInfo.FileName = $taskPython
    $taskInfo.WorkingDirectory = $taskRoot
    $taskInfo.Arguments = '-I -B -X utf8 scripts/host.py ' + $Action
    if ($Action -eq 'start' -and $NoBrowser) { $taskInfo.Arguments += ' --no-browser' }
    $taskInfo.UseShellExecute = $false
    $taskInfo.CreateNoWindow = $true
    $taskInfo.RedirectStandardOutput = $true
    $taskInfo.RedirectStandardError = $true
    $taskInfo.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $taskInfo.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    $taskProcess = [Diagnostics.Process]::Start($taskInfo)
    $taskStdout = @{Reader=$taskProcess.StandardOutput;Buffer=[char[]]::new(4096);Kept=[Text.StringBuilder]::new($taskLimit);Total=[long]0;Ended=$false;Pending=$null}
    $taskStderr = @{Reader=$taskProcess.StandardError;Buffer=[char[]]::new(4096);Kept=[Text.StringBuilder]::new($taskLimit);Total=[long]0;Ended=$false;Pending=$null}
    $taskStdout.Pending = $taskStdout.Reader.ReadAsync($taskStdout.Buffer,0,4096)
    $taskStderr.Pending = $taskStderr.Reader.ReadAsync($taskStderr.Buffer,0,4096)
    $taskStage = 'python_wait'
    $taskClock = [Diagnostics.Stopwatch]::StartNew()
    try {
        while (-not $taskStdout.Ended -or -not $taskStderr.Ended) {
            Read-LauncherStream $taskStdout
            Read-LauncherStream $taskStderr
            if ($taskStdout.Ended -and $taskStderr.Ended) { break }
            $remaining = 145000 - [int]$taskClock.ElapsedMilliseconds
            if ($remaining -le 0) {
                $taskStatus = 'pending'; $taskFailure = 'Timeout'
                throw [TimeoutException]::new('Launcher deadline reached; no process was terminated.')
            }
            $pending = [Threading.Tasks.Task[]]@($(if (-not $taskStdout.Ended) { $taskStdout.Pending }); $(if (-not $taskStderr.Ended) { $taskStderr.Pending }))
            if ([Threading.Tasks.Task]::WaitAny($pending,$remaining) -eq -1) {
                $taskStatus = 'pending'; $taskFailure = 'Timeout'
                throw [TimeoutException]::new('Launcher deadline reached; no process was terminated.')
            }
        }
        $remaining = [Math]::Max(0,145000 - [int]$taskClock.ElapsedMilliseconds)
        if (-not $taskProcess.WaitForExit($remaining)) {
            $taskStatus = 'pending'; $taskFailure = 'Timeout'
            throw [TimeoutException]::new('Launcher deadline reached; no process was terminated.')
        }
    } finally {
        $taskOutputText = Protect-LauncherText $taskStdout.Kept.ToString()
        $taskErrorText = Protect-LauncherText $taskStderr.Kept.ToString()
        $taskOutputTruncated = $taskStdout.Total -gt $taskLimit -or $taskOutputText.Length -gt $taskLimit
        $taskErrorTruncated = $taskStderr.Total -gt $taskLimit -or $taskErrorText.Length -gt $taskLimit
        if ($taskOutputText.Length -gt $taskLimit) { $taskOutputText = $taskOutputText.Substring(0,$taskLimit) }
        if ($taskErrorText.Length -gt $taskLimit) { $taskErrorText = $taskErrorText.Substring(0,$taskLimit) }
    }
    [Console]::Out.Write($taskOutputText)
    [Console]::Error.Write($taskErrorText)
    $taskConsoleStreamsWritten = $true
    $taskExitCode = $taskProcess.ExitCode
    if ($taskExitCode -eq 0) { $taskStatus = 'completed'; $taskStage = 'completed'; $taskFailure = $null }
    else { $taskStage = 'python_exit'; $taskFailure = 'ProcessExit' }
} catch {
    if ($taskUnsafePath) { $taskStage = 'path_validation'; $taskFailure = 'UnsafePath' }
    elseif ($taskFailure -notin @('RuntimeMissing','SourceMissing','Timeout')) { $taskFailure = Get-LauncherFailureType $_ }
    if ($taskStatus -ne 'pending') { $taskStatus = 'failed' }
    $taskExitCode = 1
    $taskMessages = @{
        path_validation='Unsafe launcher path. Use an ordinary local folder without links.'
        runtime='Ariadne Python runtime is unavailable. Complete Setup Ariadne.cmd first.'
        source='Ariadne host source is unavailable. Extract the complete starter folder.'
        python_start='Python could not start. Inspect the private launcher diagnostic.'
        python_wait='Ariadne startup is pending or failed. Inspect data/host/status.json before another attempt; no process was terminated.'
        diagnostic='Launcher diagnostic preparation failed. Inspect this console error before trying again.'
    }
    $taskMessage = $taskMessages[$taskStage]
    if ($null -eq $taskMessage) { $taskMessage = 'Ariadne launcher failed. Inspect the private launcher diagnostic.' }
    [Console]::Error.WriteLine('Ariadne launcher: ' + $taskMessage)
    $taskFailureNote = $taskMessage
} finally {
    if (-not $taskConsoleStreamsWritten) { [Console]::Out.Write($taskOutputText); [Console]::Error.Write($taskErrorText) }
    $taskDiagnosticAvailable = $false
    try {
        if ($null -ne $taskLog) {
            $taskLog.WriteLine('Action: ' + $Action + '; stage: ' + $taskStage + '; exit: ' + $taskExitCode)
            $taskLog.WriteLine('Launcher note: ' + $taskFailureNote)
            $taskLog.WriteLine('--- bounded private stdout ---')
            $taskLog.WriteLine($taskOutputText)
            $taskLog.WriteLine('--- bounded private stderr ---')
            $taskLog.WriteLine($taskErrorText)
            $taskLog.Flush()
            $taskLog.BaseStream.Flush($true)
            $summary = [ordered]@{schema=1;kind='ariadne-launcher-attempt';attempt_id=$taskAttempt;action=$Action;stage=$taskStage;status=$taskStatus;exit_code=$taskExitCode;failure_type=$taskFailure;interpreter=$taskInterpreter;no_browser=[bool]$NoBrowser;timeout_seconds=145;stdout_characters=$taskOutputText.Length;stderr_characters=$taskErrorText.Length;stdout_total_characters=$(if ($null -ne $taskStdout) { $taskStdout.Total } else { 0 });stderr_total_characters=$(if ($null -ne $taskStderr) { $taskStderr.Total } else { 0 });stdout_complete=($null -ne $taskStdout -and $taskStdout.Ended);stderr_complete=($null -ne $taskStderr -and $taskStderr.Ended);stdout_truncated=$taskOutputTruncated;stderr_truncated=$taskErrorTruncated;max_stream_characters=$taskLimit;diagnostic_available=$true;retries=0;process_terminated=$false}
            $receiptPath = Join-Path $taskLogDirectory $taskReceiptName
            $receiptStream = [IO.FileStream]::new($receiptPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
            try {
                $receiptWriter = [IO.StreamWriter]::new($receiptStream,[Text.UTF8Encoding]::new($false))
                try { $receiptWriter.WriteLine(($summary | ConvertTo-Json -Depth 4)); $receiptWriter.Flush(); $receiptStream.Flush($true) }
                finally { $receiptWriter.Dispose() }
            } finally { $receiptStream.Dispose() }
            $taskDiagnosticAvailable = $true
        }
    } catch { [Console]::Error.WriteLine('Ariadne launcher: diagnostic publication failed; the original exit code is unchanged.') }
    finally {
        if ($null -ne $taskLog) { $taskLog.Dispose() }
        if ($null -ne $taskProcess) { $taskProcess.Dispose() }
        for ($index=$taskHandles.Count-1; $index -ge 0; $index--) { [void]$taskNative.GetMethod('CloseHandle').Invoke($null,[object[]]@($taskHandles[$index])) }
    }
    if ($taskExitCode -ne 0 -and $taskDiagnosticAvailable) {
        [Console]::Error.WriteLine('Private diagnostic: data\host\' + $taskLogName)
        [Console]::Error.WriteLine('Safe stage/type/exit summary: data\host\' + $taskReceiptName)
    }
}
exit $taskExitCode