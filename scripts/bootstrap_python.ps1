param([switch]$Check)
$ErrorActionPreference = 'Stop'
# Official CPython NuGet full-runtime profile. NuGet is a ZIP, not an installer.
# https://docs.python.org/3.13/using/windows.html#the-nuget-org-packages
# https://github.com/python/cpython/blob/v3.13.17/PC/layout/support/options.py
$bootstrapVersion = '3.13.17'
$bootstrapUrl = 'https://api.nuget.org/v3-flatcontainer/python/3.13.17/python.3.13.17.nupkg'
$bootstrapBytes = [long]14826465
$bootstrapSha256 = 'dce409744b088f94c6e37ba41f8100c384c27e029e843237364c602d30d475e5'
$bootstrapRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$bootstrapAttempt = [Guid]::NewGuid().ToString('N')
$bootstrapHandles = [Collections.Generic.List[IntPtr]]::new()
$bootstrapPinnedDirectories = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$bootstrapUnsafePath = $false
$bootstrapNative = $null

function New-BootstrapNative {
    # Reflection.Emit keeps native declarations in memory. Add-Type on Windows
    # PowerShell 5.1 would create compiler artifacts outside a fresh box.
    $assembly = [AppDomain]::CurrentDomain.DefineDynamicAssembly([Reflection.AssemblyName]::new('AriadneBootstrap' + $bootstrapAttempt), [Reflection.Emit.AssemblyBuilderAccess]::Run)
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

function Reject-BootstrapPath {
    $script:bootstrapUnsafePath = $true
    throw [IO.IOException]::new('Bootstrap paths must be ordinary local files and folders without links.')
}

function Get-BootstrapAttributes([string]$Path) {
    try { return [int][IO.File]::GetAttributes($Path) }
    catch [IO.FileNotFoundException] { return -1 }
    catch [IO.DirectoryNotFoundException] { return -1 }
}

function Assert-BootstrapHandle([IntPtr]$Handle, [bool]$Directory) {
    $buffer = [Runtime.InteropServices.Marshal]::AllocHGlobal(52)
    try {
        if (-not $script:bootstrapNative.GetMethod('GetFileInformationByHandle').Invoke($null, [object[]]@($Handle,$buffer))) { throw [IO.IOException]::new('Could not verify a launcher handle.') }
        $attributes = [Runtime.InteropServices.Marshal]::ReadInt32($buffer, 0)
        $links = [Runtime.InteropServices.Marshal]::ReadInt32($buffer, 40)
        if (($attributes -band 0x400) -ne 0 -or (($attributes -band 0x10) -ne 0) -ne $Directory -or (-not $Directory -and $links -ne 1)) { Reject-BootstrapPath }
    } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($buffer) }
}

function Pin-BootstrapDirectory([string]$Path, [bool]$Create = $false) {
    $full = [IO.Path]::GetFullPath($Path)
    $drive = [IO.Path]::GetPathRoot($full)
    if ($drive -ieq 'E:\' -or $drive.StartsWith('\\')) { Reject-BootstrapPath }
    $parts = [Collections.Generic.List[string]]::new()
    $parts.Add($drive)
    $walk = $drive
    foreach ($part in $full.Substring($drive.Length).Split([IO.Path]::DirectorySeparatorChar)) {
        if ($part.Length -eq 0) { continue }
        $walk = Join-Path $walk $part
        $parts.Add($walk)
    }
    foreach ($component in $parts) {
        if ($script:bootstrapPinnedDirectories.Contains($component)) { continue }
        $attributes = Get-BootstrapAttributes $component
        if ($attributes -eq -1) {
            if (-not $Create) { return $false }
            # The preceding directory is already pinned against replacement.
            [void][IO.Directory]::CreateDirectory($component)
        } elseif (($attributes -band 0x400) -ne 0 -or ($attributes -band 0x10) -eq 0) { Reject-BootstrapPath }
        # FILE_LIST_DIRECTORY and no FILE_SHARE_DELETE pin every ancestor.
        # OPEN_REPARSE_POINT verifies the final component without following it.
        $handle = $script:bootstrapNative.GetMethod('CreateFileW').Invoke($null, [object[]]@($component,[uint32]1,[uint32]3,[IntPtr]::Zero,[uint32]3,[uint32]0x02200000,[IntPtr]::Zero))
        if ($handle -eq [IntPtr]::new(-1)) { throw [IO.IOException]::new('Could not pin a launcher directory.') }
        $script:bootstrapHandles.Add($handle)
        Assert-BootstrapHandle $handle $true
        [void]$script:bootstrapPinnedDirectories.Add($component)
    }
    return $true
}

function Pin-BootstrapFile([string]$Path) {
    if (-not (Pin-BootstrapDirectory ([IO.Path]::GetDirectoryName($Path)))) { return $false }
    $attributes = Get-BootstrapAttributes $Path
    if ($attributes -eq -1) { return $false }
    if (($attributes -band 0x400) -ne 0 -or ($attributes -band 0x10) -ne 0) { Reject-BootstrapPath }
    $handle = $script:bootstrapNative.GetMethod('CreateFileW').Invoke($null, [object[]]@($Path,[uint32]2147483648,[uint32]1,[IntPtr]::Zero,[uint32]3,[uint32]0x00200000,[IntPtr]::Zero))
    if ($handle -eq [IntPtr]::new(-1)) { throw [IO.IOException]::new('Could not pin a launcher file.') }
    $script:bootstrapHandles.Add($handle)
    Assert-BootstrapHandle $handle $false
    return $true
}


function Get-BootstrapHash([IO.Stream]$Stream) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($Stream))).Replace('-','').ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}

function Test-BootstrapOwner {
    foreach ($name in @('data\server.token','data\ariadne.sqlite3','data\host\status.json')) {
        $path = Join-Path $script:bootstrapRoot $name
        if (-not (Pin-BootstrapDirectory ([IO.Path]::GetDirectoryName($path)))) { continue }
        if ((Get-BootstrapAttributes $path) -ne -1) { return $true }
    }
    return $false
}

function Assert-BootstrapName([string]$Name) {
    if ([string]::IsNullOrEmpty($Name) -or $Name.Length -gt 240 -or $Name.StartsWith('/') -or $Name.Contains('\') -or $Name -match '[^\x20-\x7e]|[<>:"|?*]') { throw 'Unsafe Python archive member.' }
    $trimmed = $Name.TrimEnd('/')
    if ($trimmed.Length -eq 0) { throw 'Unsafe Python archive member.' }
    foreach ($part in $trimmed.Split('/')) {
        if ($part.Length -eq 0 -or $part -in @('.','..') -or $part.EndsWith('.') -or $part.EndsWith(' ') -or $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') { throw 'Ambiguous Python archive member.' }
    }
    return $trimmed
}

function Get-BootstrapEntries([IO.Compression.ZipArchive]$Archive) {
    if ($Archive.Entries.Count -gt 5000) { throw 'Python archive has too many entries.' }
    $names = [Collections.Generic.Dictionary[string,bool]]::new([StringComparer]::OrdinalIgnoreCase)
    $expanded = [long]0
    $selected = [Collections.Generic.List[object]]::new()
    foreach ($entry in $Archive.Entries) {
        $name = Assert-BootstrapName $entry.FullName
        $directory = $entry.FullName.EndsWith('/')
        if ($names.ContainsKey($name)) { throw 'Python archive has duplicate aliases.' }
        $names.Add($name,$directory)
        $attributes = [uint32]([long]$entry.ExternalAttributes -band 0xffffffffL)
        $mode = ($attributes -shr 16) -band 0xf000
        if (($mode -ne 0 -and $mode -ne 0x8000 -and $mode -ne 0x4000) -or ($attributes -band 0x400) -ne 0) { throw 'Python archive contains a link or unsupported entry.' }
        if ($entry.Length -lt 0 -or $entry.Length -gt 32MB -or ($directory -and $entry.Length -ne 0)) { throw 'Python archive member exceeds its size bound.' }
        $expanded += $entry.Length
        if ($expanded -gt 128MB) { throw 'Python archive exceeds its expansion bound.' }
        if ($name.StartsWith('tools/',[StringComparison]::Ordinal)) {
            $relative = $name.Substring(6)
            if (-not $directory) { $selected.Add(@{Entry=$entry;Relative=$relative}) }
        }
    }
    foreach ($name in $names.Keys) {
        $parent = $name
        while ($parent.Contains('/')) {
            $parent = $parent.Substring(0,$parent.LastIndexOf('/'))
            if ($names.ContainsKey($parent) -and -not $names[$parent]) { throw 'Python archive aliases a file and directory.' }
        }
    }
    foreach ($required in @('python.exe','python313.dll','vcruntime140.dll','vcruntime140_1.dll','DLLs/_ssl.pyd','DLLs/_sqlite3.pyd','Lib/site-packages/pip/__init__.py','LICENSE.txt')) {
        if (-not $names.ContainsKey('tools/'+$required) -or $names['tools/'+$required]) { throw 'Python archive is missing its full-runtime profile.' }
    }
    return ,$selected
}

function Save-BootstrapArchive([string]$ArchivePath) {
    $partial = $ArchivePath + '.partial'
    if ((Get-BootstrapAttributes $partial) -ne -1) { throw 'Previous Python download is held. Inspect its .partial file before retrying.' }
    if (Pin-BootstrapFile $ArchivePath) {
        $source = [IO.File]::Open($ArchivePath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try {
            if ($source.Length -ne $script:bootstrapBytes -or (Get-BootstrapHash $source) -ne $script:bootstrapSha256) { throw 'Cached Python archive failed its pinned size/hash. It was preserved.' }
        } finally { $source.Dispose() }
        return
    }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $request = [Net.HttpWebRequest]::Create($script:bootstrapUrl)
    $request.AllowAutoRedirect = $false
    $request.Proxy = $null
    $request.Timeout = 45000
    $request.ReadWriteTimeout = 15000
    $request.UserAgent = 'Ariadne-Python-Bootstrap/1'
    $response = $null; $input = $null; $output = $null
    try {
        $response = $request.GetResponse()
        if ([int]$response.StatusCode -ne 200 -or $response.ResponseUri.AbsoluteUri -ne $script:bootstrapUrl -or ($response.ContentLength -ne -1 -and $response.ContentLength -ne $script:bootstrapBytes)) { throw 'Python archive returned an unexpected response.' }
        $input = $response.GetResponseStream()
        $output = [IO.File]::Open($partial,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
        Assert-BootstrapHandle $output.SafeFileHandle.DangerousGetHandle() $false
        $buffer = [byte[]]::new(65536); $total = [long]0; $clock = [Diagnostics.Stopwatch]::StartNew()
        while (($count = $input.Read($buffer,0,$buffer.Length)) -gt 0) {
            $total += $count
            if ($total -gt $script:bootstrapBytes -or $clock.ElapsedMilliseconds -gt 120000) { throw 'Python archive download exceeded its bound.' }
            $output.Write($buffer,0,$count)
        }
        $output.Flush($true); $output.Dispose(); $output = $null
        if ($total -ne $script:bootstrapBytes) { throw 'Python archive download is incomplete; partial file preserved.' }
        $verify = [IO.File]::Open($partial,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try {
            Assert-BootstrapHandle $verify.SafeFileHandle.DangerousGetHandle() $false
            if ((Get-BootstrapHash $verify) -ne $script:bootstrapSha256) { throw 'Python archive checksum mismatch; partial file preserved.' }
        } finally { $verify.Dispose() }
        if ((Get-BootstrapAttributes $ArchivePath) -ne -1) { throw 'Python archive destination appeared; nothing was replaced.' }
        [IO.File]::Move($partial,$ArchivePath)
        if (-not (Pin-BootstrapFile $ArchivePath)) { throw 'Verified Python archive is unavailable.' }
    } finally {
        if ($null -ne $output) { $output.Dispose() }
        if ($null -ne $input) { $input.Dispose() }
        if ($null -ne $response) { $response.Dispose() }
    }
}

function Test-BootstrapRuntime([string]$Directory,[string]$Temporary) {
    $code = "import sys,ssl,sqlite3,pip,json,struct,sysconfig; from pathlib import Path; p=Path(sys.argv[1]).resolve(); assert Path(sys.prefix).resolve()==p and Path(sys.base_prefix).resolve()==p; assert Path(sys.executable).resolve()==p/'python.exe'; assert sys.version_info[:3]==(3,13,17) and struct.calcsize('P')==8 and sysconfig.get_platform()=='win-amd64'; assert pip.__version__=='26.2.2'; assert sqlite3.connect(':memory:').execute('select 1').fetchone()==(1,); ssl.create_default_context(); print(json.dumps({'version':sys.version.split()[0],'platform':sysconfig.get_platform(),'ssl':ssl.OPENSSL_VERSION,'sqlite':sqlite3.sqlite_version,'pip':pip.__version__,'prefix_local':True}))"
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = Join-Path $Directory 'python.exe'
    $info.Arguments = '-I -B -X utf8 -c "' + $code + '" "' + $Directory + '"'
    $info.WorkingDirectory = $Directory
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.EnvironmentVariables.Clear()
    foreach ($key in @('SystemRoot','WINDIR','COMSPEC')) { if ($null -ne [Environment]::GetEnvironmentVariable($key)) { $info.EnvironmentVariables[$key] = [Environment]::GetEnvironmentVariable($key) } }
    $info.EnvironmentVariables['PATH'] = Join-Path $env:SystemRoot 'System32'
    foreach ($key in @('TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA')) { $info.EnvironmentVariables[$key] = $Temporary }
    $info.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    $process = [Diagnostics.Process]::Start($info)
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(20000)) { $process.Kill(); $process.WaitForExit(); throw 'New local Python probe timed out.' }
        if ($process.ExitCode -ne 0) { throw 'New local Python failed the SSL/sqlite/pip/prefix probe.' }
        if ($stdout.Result.Length -gt 8192 -or $stderr.Result.Length -gt 8192) { throw 'New local Python probe output exceeded its bound.' }
        return $stdout.Result | ConvertFrom-Json
    } finally { $process.Dispose() }
}

function Invoke-PythonBootstrap([switch]$CheckOnly) {
    if (-not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'Bootstrap requires native Windows x64 PowerShell.' }
    $script:bootstrapNative = New-BootstrapNative
    if (-not (Pin-BootstrapDirectory $script:bootstrapRoot)) { throw 'Extract the complete Ariadne starter into an ordinary local folder.' }
    $target = Join-Path $script:bootstrapRoot 'runtime\python'
    $cache = Join-Path $script:bootstrapRoot 'runtime\downloads'
    $archivePath = Join-Path $cache ('python.'+$script:bootstrapVersion+'.nupkg')
    $stage = Join-Path $script:bootstrapRoot ('runtime\python-bootstrap-'+$script:bootstrapVersion+'.partial')
    $owner = Test-BootstrapOwner
    if ($CheckOnly) {
        $state = [ordered]@{schema=1;status='missing';version=$script:bootstrapVersion;archive_sha256=$script:bootstrapSha256;runtime_present=($false);private_installation_present=$owner;downloads=$false;processes_started=$false}
        if ((Pin-BootstrapDirectory $target) -and (Pin-BootstrapFile (Join-Path $target 'python.exe'))) { $state.runtime_present=$true; $state.status='present_unverified' }
        if ($owner) { $state.status='existing_installation' }
        return $state
    }
    if ($owner) { throw 'Existing private installation detected. Python bootstrap will not change it.' }
    if ((Get-BootstrapAttributes $target) -ne -1) { throw 'Python runtime destination already exists. Nothing was replaced.' }
    if ((Get-BootstrapAttributes $stage) -ne -1) { throw 'Previous Python extraction is held. Inspect the .partial directory before retrying.' }
    [void](Pin-BootstrapDirectory $cache $true)
    [void](Pin-BootstrapDirectory (Join-Path $script:bootstrapRoot 'runtime\tmp') $true)
    Save-BootstrapArchive $archivePath
    [void][Reflection.Assembly]::LoadWithPartialName('System.IO.Compression')
    $source = [IO.File]::Open($archivePath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    $archive = $null
    try {
        $archive = [IO.Compression.ZipArchive]::new($source,[IO.Compression.ZipArchiveMode]::Read,$true)
        $entries = Get-BootstrapEntries $archive
        [void](Pin-BootstrapDirectory $stage $true)
        $files = [ordered]@{}
        foreach ($item in $entries) {
            $relative = $item.Relative
            $path = [IO.Path]::GetFullPath((Join-Path $stage $relative.Replace('/',[IO.Path]::DirectorySeparatorChar)))
            if (-not $path.StartsWith($stage+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Python extraction escaped its stage.' }
            [void](Pin-BootstrapDirectory ([IO.Path]::GetDirectoryName($path)) $true)
            $input = $item.Entry.Open(); $output = $null
            try {
                $output = [IO.File]::Open($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::ReadWrite,[IO.FileShare]::Read)
                Assert-BootstrapHandle $output.SafeFileHandle.DangerousGetHandle() $false
                $buffer=[byte[]]::new(65536);$total=[long]0
                while (($count=$input.Read($buffer,0,$buffer.Length)) -gt 0) { $total+=$count;if ($total -gt $item.Entry.Length) { throw 'Python entry exceeded its declared length.' };$output.Write($buffer,0,$count) }
                if ($total -ne $item.Entry.Length) { throw 'Python entry was truncated.' }
                $output.Flush($true);$output.Position=0
                $files[$relative]=Get-BootstrapHash $output
            } finally { if ($null -ne $output) { $output.Dispose() };$input.Dispose() }
        }
        $manifest=[ordered]@{schema=1;version=$script:bootstrapVersion;archive_sha256=$script:bootstrapSha256;files=$files}
        # Exact upstream file hashes and source identity; no local paths.
        $manifestPath=Join-Path $stage 'BOOTSTRAP-MANIFEST.json'
        $manifestBytes=[Text.UTF8Encoding]::new($false).GetBytes(($manifest|ConvertTo-Json -Depth 5)+"`n")
        $manifestStream=[IO.File]::Open($manifestPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
        try { $manifestStream.Write($manifestBytes,0,$manifestBytes.Length);$manifestStream.Flush($true) } finally { $manifestStream.Dispose() }
        $probe=Test-BootstrapRuntime $stage (Join-Path $script:bootstrapRoot 'runtime\tmp')
    } finally { if ($null -ne $archive) { $archive.Dispose() };$source.Dispose() }
    # Release extraction pins before a same-parent exclusive directory rename.
    # Both absolute paths are verified inside this box; no recursive shell move.
    for ($i=$script:bootstrapHandles.Count-1;$i -ge 0;$i--) { [void]$script:bootstrapNative.GetMethod('CloseHandle').Invoke($null,[object[]]@($script:bootstrapHandles[$i])) }
    $script:bootstrapHandles.Clear();$script:bootstrapPinnedDirectories.Clear()
    [void](Pin-BootstrapDirectory (Join-Path $script:bootstrapRoot 'runtime'))
    $stageFull=[IO.Path]::GetFullPath($stage);$targetFull=[IO.Path]::GetFullPath($target)
    $expectedParent=[IO.Path]::GetFullPath((Join-Path $script:bootstrapRoot 'runtime'))
    if ([IO.Path]::GetDirectoryName($stageFull) -ne $expectedParent -or [IO.Path]::GetDirectoryName($targetFull) -ne $expectedParent -or (Get-BootstrapAttributes $stageFull) -band 0x400 -or (Get-BootstrapAttributes $targetFull) -ne -1) { throw 'Python publication paths changed; stage preserved.' }
    if (Test-BootstrapOwner) { throw 'Private installation appeared during setup; stage preserved.' }
    [IO.Directory]::Move($stageFull,$targetFull)
    [void](Pin-BootstrapDirectory $targetFull)
    $probe=Test-BootstrapRuntime $targetFull (Join-Path $script:bootstrapRoot 'runtime\tmp')
    return [ordered]@{schema=1;status='installed';version=$script:bootstrapVersion;archive_sha256=$script:bootstrapSha256;files=$files.Count;runtime='runtime/python';probe=$probe;system_install=$false}
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-PythonBootstrap -CheckOnly:$Check | ConvertTo-Json -Depth 6; exit 0 }
    catch { [Console]::Error.WriteLine('Ariadne Python bootstrap failed. No existing runtime was replaced; partial artifacts are held for inspection. '+$_.Exception.Message); exit 1 }
    finally { if ($null -ne $bootstrapNative) { for ($i=$bootstrapHandles.Count-1;$i -ge 0;$i--) { [void]$bootstrapNative.GetMethod('CloseHandle').Invoke($null,[object[]]@($bootstrapHandles[$i])) } } }
}
