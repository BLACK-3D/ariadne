param([switch]$Install)
# Preview writes only inside this box. -Install explicitly registers one Windows task.
$ErrorActionPreference = 'Stop'
$boxRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd('\')
if ($boxRoot.StartsWith('E:', [StringComparison]::OrdinalIgnoreCase)) { throw 'E: is excluded.' }
function Assert-BoxPath([string]$value) {
    $resolvedPath = [IO.Path]::GetFullPath($value)
    if ($resolvedPath -ne $boxRoot -and -not $resolvedPath.StartsWith($boxRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Path leaves this box.' }
    $componentPath = $resolvedPath
    while ($componentPath) {
        if (Test-Path -LiteralPath $componentPath) {
            $item = Get-Item -LiteralPath $componentPath -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.LinkType) { throw 'Linked task paths are not allowed.' }
        }
        $parentPath = Split-Path -Parent $componentPath
        if ($parentPath -eq $componentPath) { break }
        $componentPath = $parentPath
    }
    return $resolvedPath
}
$pythonPath = Assert-BoxPath (Join-Path $boxRoot 'runtime\python\python.exe')
$scriptPath = Assert-BoxPath (Join-Path $boxRoot 'scripts\briefing.py')
$dataPath = Assert-BoxPath (Join-Path $boxRoot 'data')
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf) -or -not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) { throw 'Bundled Python and briefing script are required.' }
$localZone = Get-TimeZone
if ($localZone.Id -ne 'Central Standard Time' -or -not $localZone.SupportsDaylightSavingTime -or -not [TimeZoneInfo]::Local.SupportsDaylightSavingTime) { throw 'This 6 AM installer requires Windows Central Standard Time with daylight-saving adjustments enabled (America/Chicago). Configure the local app timer for other supported zones.' }
$hashProvider = [Security.Cryptography.SHA256]::Create()
try { $rootHash = -join ($hashProvider.ComputeHash([Text.Encoding]::UTF8.GetBytes($boxRoot.ToLowerInvariant())) | ForEach-Object { $_.ToString('x2') }) } finally { $hashProvider.Dispose() }
$taskName = 'Ariadne Daily Brief ' + $rootHash.Substring(0,12)
$arguments = '-I -B "' + $scriptPath + '" due --data-dir "' + $dataPath + '"'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$nextMorning = (Get-Date).Date.AddHours(6)
if ($nextMorning -le (Get-Date)) { $nextMorning = $nextMorning.AddDays(1) }
function Escape-Xml([string]$value) { return [Security.SecurityElement]::Escape($value) }
function Get-PrincipalSid([string]$value) {
    if ($value -match '^S-\d-') { return [Security.Principal.SecurityIdentifier]::new($value).Value }
    return ([Security.Principal.NTAccount]::new($value)).Translate([Security.Principal.SecurityIdentifier]).Value
}
$boundary = $nextMorning.ToString('yyyy-MM-ddTHH:mm:ss')
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Ariadne fixed local 6 AM brief. No model calls. Respects pause and failure holds. Logged-in current user; no stored password.</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>$boundary</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Owner"><UserId>$(Escape-Xml $identity)</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable><IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings><AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden><RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>true</WakeToRun><ExecutionTimeLimit>PT10M</ExecutionTimeLimit></Settings>
  <Actions Context="Owner"><Exec><Command>$(Escape-Xml $pythonPath)</Command><Arguments>$(Escape-Xml $arguments)</Arguments><WorkingDirectory>$(Escape-Xml $boxRoot)</WorkingDirectory></Exec></Actions>
</Task>
"@
[xml]$parsed = $xml
$outputFolder = Assert-BoxPath (Join-Path $dataPath 'scheduling')
[IO.Directory]::CreateDirectory($outputFolder) | Out-Null
$xmlPath = Assert-BoxPath (Join-Path $outputFolder 'morning-task.xml')
[IO.File]::WriteAllText($xmlPath, $xml, [Text.Encoding]::Unicode)
if (-not $Install) {
    Write-Output "Preview saved: $xmlPath"
    Write-Output "Windows task is not registered. Task: $taskName; 06:00 local Central time; WakeToRun requested."
    exit 0
}
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    $actions = @($existing.Actions)
    if ($actions.Count -ne 1 -or $actions[0].Execute -ne $pythonPath -or $actions[0].Arguments -ne $arguments -or $actions[0].WorkingDirectory -ne $boxRoot -or (Get-PrincipalSid $existing.Principal.UserId) -ne $identity -or [string]$existing.Principal.LogonType -ne 'Interactive' -or [string]$existing.Principal.RunLevel -ne 'Limited') { throw 'An unrelated task already owns this name; it will not be overwritten.' }
}
Register-ScheduledTask -TaskName $taskName -Xml $xml -Force | Out-Null
$installed = Get-ScheduledTask -TaskName $taskName
if (-not $installed.Settings.WakeToRun -or $installed.Settings.ExecutionTimeLimit -ne 'PT10M' -or @($installed.Actions).Count -ne 1 -or $installed.Actions[0].Execute -ne $pythonPath -or $installed.Actions[0].Arguments -ne $arguments -or $installed.Actions[0].WorkingDirectory -ne $boxRoot -or (Get-PrincipalSid $installed.Principal.UserId) -ne $identity -or [string]$installed.Principal.LogonType -ne 'Interactive' -or [string]$installed.Principal.RunLevel -ne 'Limited') { throw 'Task registration verification failed.' }
$triggers = @($installed.Triggers)
if ($triggers.Count -ne 1 -or -not $triggers[0].Enabled -or $triggers[0].DaysInterval -ne 1 -or ([datetime]$triggers[0].StartBoundary).TimeOfDay -ne [TimeSpan]::FromHours(6)) { throw 'Task trigger verification failed.' }
$receiptPath = Assert-BoxPath (Join-Path $outputFolder 'morning-task-receipt.json')
$receipt = @{installed=$true; task_name=$taskName; schedule='06:00 America/Chicago'; wake_from_sleep_requested=$true; root=$boxRoot; created_at=(Get-Date).ToUniversalTime().ToString('o'); limits='Requires logged-in owner, supported hardware and permitted Windows wake timers. Cannot power on a shut-down PC. No power-plan changes were made.'}
[IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
Write-Output "Registered and verified: $taskName. Receipt: $receiptPath"
