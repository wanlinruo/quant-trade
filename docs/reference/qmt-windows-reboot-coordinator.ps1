# Default-off #601 Windows validation coordinator (reviewed, run-agnostic).
#
# Bound only through fixture.json and requested.json in its own audit root.
# It creates the private run-bound named mapping, publishes the coordinator
# marker at offset 6144 BEFORE starting the driver (so a QMT model that
# auto-starts later always finds it), and is the only writer of first-go.json.
# mode=reboot requires an advanced boot time and a QMT started after boot;
# mode=rehearsal requires the unchanged boot time and the recorded QMT PID.
# No account, market, trading or cancel calls; no automatic replay.
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$trustedExe = 'C:\self\qsys-qmt-2.1.33.0\bin.x64\XtItClient.exe'
$mapping = $null; $view = $null; $driver = $null; $stage = 'guard'; $taskName = $null

# Publish atomically: the driver polls for existence, so the final name must
# only appear after the bytes are complete and the writer handle is closed.
# File.Move fails if the destination exists, so nothing is ever overwritten.
function Write-NewJson([string]$Name, $Value) {
    $path = Join-Path $root $Name
    $staging = Join-Path $root ('.staging-' + $Name)
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 8 -Compress))
    $stream = [IO.File]::Open($staging, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    [IO.File]::Move($staging, $path)
}

function Read-Json([string]$Name) {
    return [IO.File]::ReadAllText((Join-Path $root $Name), [Text.Encoding]::UTF8) | ConvertFrom-Json
}

# Read a driver-published file, tolerating a writer that has not closed yet.
function Read-PublishedBytes([string]$Name, [datetime]$Deadline) {
    while ($true) {
        try { return [IO.File]::ReadAllBytes((Join-Path $root $Name)) }
        catch [IO.IOException] { if ((Get-Date) -ge $Deadline) { throw ('published file unreadable: ' + $Name) } }
        catch [UnauthorizedAccessException] { if ((Get-Date) -ge $Deadline) { throw ('published file unreadable: ' + $Name) } }
        Start-Sleep -Milliseconds 100
    }
}

try {
    $fixture = Read-Json 'fixture.json'
    $run = [string]$fixture.first
    $mode = [string]$fixture.mode
    $taskName = [string]$fixture.coordinator_task
    if ($run -cnotmatch '^[0-9a-f]{32}$') { throw 'fixture run binding invalid' }
    if ($mode -cne 'reboot' -and $mode -cne 'rehearsal') { throw 'fixture mode invalid' }
    if ($taskName -cne ('qsys-issue601-' + $mode + '-coordinate-' + $run.Substring(0, 8))) {
        throw 'fixture coordinator task binding invalid'
    }
    if ((Get-Process -Id $PID).SessionId -ne 1) { throw 'interactive session 1 required' }
    $acl = Get-Acl -LiteralPath $root
    if (-not $acl.AreAccessRulesProtected) { throw 'private audit root required' }
    $trustees = @($acl.Access | ForEach-Object { $_.IdentityReference.Value } | Sort-Object -Unique)
    if (($trustees -join '|') -ne 'BUILTIN\Administrators|NT AUTHORITY\SYSTEM') { throw 'audit trustees changed' }
    $request = Read-Json 'requested.json'
    if ($request.run_id -cne $run -or $request.mode -cne $mode) { throw 'authorization binding changed' }
    if ($mode -ceq 'reboot' -and $request.authorized_reboot -ne $true) { throw 'reboot not authorized' }
    if ($mode -ceq 'rehearsal' -and $request.authorized_rehearsal -ne $true) { throw 'rehearsal not authorized' }
    foreach ($property in $request.hashes.PSObject.Properties) {
        $path = Join-Path $root $property.Name
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $property.Value) {
            throw ('source changed: ' + $property.Name)
        }
    }
    $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
    $recordedBoot = [string]$request.pre_start.boot_time
    if ($mode -ceq 'reboot') {
        if ($boot.ToString('o') -eq $recordedBoot) { throw 'Windows did not reboot' }
        if ($boot -le [DateTime]::Parse($recordedBoot)) { throw 'boot time did not advance' }
    } elseif ($boot.ToString('o') -ne $recordedBoot) { throw 'rehearsal boot time changed' }
    if (Test-Path -LiteralPath (Join-Path $root 'coordinator-started.json')) { throw 'coordinator already started; no replay' }
    Write-NewJson 'coordinator-started.json' ([ordered]@{
        at = (Get-Date).ToString('o'); boot_time = $boot.ToString('o'); pid = $PID; session_id = 1
        run_id = $run; mode = $mode; automatic_replay = $false; account_calls = 0; trading_calls = 0
    })

    $stage = 'private_mapping'
    $security = [IO.MemoryMappedFiles.MemoryMappedFileSecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    foreach ($sid in @(
        [Security.Principal.WindowsIdentity]::GetCurrent().User,
        [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    )) {
        $rule = $security.AccessRuleFactory(
            $sid, [int][IO.MemoryMappedFiles.MemoryMappedFileRights]::FullControl, $false,
            [Security.AccessControl.InheritanceFlags]::None,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow)
        $security.AddAccessRule($rule)
    }
    $mapping = [IO.MemoryMappedFiles.MemoryMappedFile]::CreateNew(
        ('Local\qsys_synthetic_intent_' + $run), 8192,
        [IO.MemoryMappedFiles.MemoryMappedFileAccess]::ReadWrite,
        [IO.MemoryMappedFiles.MemoryMappedFileOptions]::None, $security,
        [IO.HandleInheritability]::None)

    $stage = 'coordinator_marker'
    $marker = [Text.Encoding]::ASCII.GetBytes('QSYS_SYNTHETIC_INTENT_ONLY:' + $run)
    $view = $mapping.CreateViewAccessor(0, 8192)
    $view.WriteArray(6144, $marker, 0, $marker.Length)
    $view.Flush()
    $readback = New-Object byte[] $marker.Length
    [void]$view.ReadArray(6144, $readback, 0, $readback.Length)
    if ([Convert]::ToBase64String($readback) -cne [Convert]::ToBase64String($marker)) { throw 'coordinator marker readback mismatch' }
    Write-NewJson 'coordinator-marker.json' ([ordered]@{
        at = (Get-Date).ToString('o'); run_id = $run; offset = 6144; written_before_driver = $true
    })

    $stage = 'driver'
    $driver = [Diagnostics.Process]::new()
    $driver.StartInfo.FileName = 'C:\qsys\.venv\Scripts\python.exe'
    $driver.StartInfo.Arguments = '-B "' + $root + '\native.py"'
    $driver.StartInfo.WorkingDirectory = $root
    $driver.StartInfo.UseShellExecute = $false
    $driver.StartInfo.CreateNoWindow = $true
    $driver.StartInfo.RedirectStandardOutput = $true
    $driver.StartInfo.RedirectStandardError = $true
    $driver.StartInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    if (-not $driver.Start()) { throw 'driver did not start' }
    # Drain pipes asynchronously so a chatty driver can never block on a full pipe.
    $stdoutTask = $driver.StandardOutput.ReadToEndAsync()
    $stderrTask = $driver.StandardError.ReadToEndAsync()
    Write-NewJson 'driver-launched.json' ([ordered]@{
        at = (Get-Date).ToString('o'); pid = $driver.Id; run_id = $run; account_calls = 0; trading_calls = 0
    })

    $stage = 'wait_driver_ready'
    $readyDeadline = (Get-Date).AddSeconds(120)
    while (-not (Test-Path -LiteralPath (Join-Path $root 'first-ready.json'))) {
        if ($driver.HasExited) { throw 'driver exited before ready' }
        if ((Get-Date) -ge $readyDeadline) { throw 'driver ready timeout' }
        Start-Sleep -Milliseconds 200
    }

    $stage = 'wait_model_scheduled'
    $scheduledPath = Join-Path $root 'model-scheduled.json'
    $modelDeadline = (Get-Date).AddSeconds(190)
    while (-not (Test-Path -LiteralPath $scheduledPath -PathType Leaf)) {
        if ($driver.HasExited) { throw 'driver exited before model schedule' }
        if ((Get-Date) -ge $modelDeadline) { throw 'run-bound scheduled marker timeout' }
        Start-Sleep -Milliseconds 200
    }
    $scheduledBytes = Read-PublishedBytes 'model-scheduled.json' $modelDeadline
    $scheduled = [Text.Encoding]::UTF8.GetString($scheduledBytes) | ConvertFrom-Json
    if ($scheduled.run -cne $run -or $scheduled.evidence -cne 'run-bound-shared-memory-marker') {
        throw 'scheduled receipt binding mismatch'
    }
    $scheduledSha = ([Security.Cryptography.SHA256]::Create().ComputeHash($scheduledBytes) |
        ForEach-Object { $_.ToString('x2') }) -join ''
    $rows = @(Get-CimInstance Win32_Process -Filter "Name = 'XtItClient.exe'" |
        Where-Object { $_.ExecutablePath -eq $trustedExe -and $_.SessionId -eq 1 })
    if ($rows.Count -ne 1) { throw 'trusted QMT process identity mismatch after scheduled marker' }
    $qmt = Get-Process -Id $rows[0].ProcessId -ErrorAction Stop
    if ($mode -ceq 'reboot') {
        if ($qmt.StartTime -lt $boot) { throw 'trusted QMT was not started after reboot' }
    } elseif ($qmt.Id -ne [int]$request.pre_start.qmt_pid) { throw 'rehearsal QMT identity changed' }

    $stage = 'open_send_gate'
    Write-NewJson 'first-go.json' ([ordered]@{
        at = (Get-Date).ToString('o'); run = $run; mode = $mode; model_scheduled_sha256 = $scheduledSha
        boot_time = $boot.ToString('o'); new_pid = $qmt.Id; new_started_at = $qmt.StartTime.ToString('o')
        automatic_start_only = ($mode -ceq 'reboot'); manual_start_fallback = $false
        formal_mode = 'simulated'; windows_restarted = ($mode -ceq 'reboot')
    })

    $stage = 'wait_driver'
    if (-not $driver.WaitForExit(125000)) { throw 'driver not terminal; no replay' }
    $driver.WaitForExit()
    [IO.File]::WriteAllText((Join-Path $root 'first-stdout.txt'), $stdoutTask.Result)
    [IO.File]::WriteAllText((Join-Path $root 'first-stderr.txt'), $stderrTask.Result)
    Write-NewJson 'driver-exit.json' ([ordered]@{
        at = (Get-Date).ToString('o'); pid = $driver.Id; exit_code = $driver.ExitCode; has_exited = $driver.HasExited
    })
    if ($driver.ExitCode -ne 0) { throw 'driver failed' }
    $result = Read-Json 'first-result.json'
    if ($result.status -cne 'success' -or $result.run -cne $run -or $result.mode -cne $mode -or
        $result.receipts.Count -ne 1 -or
        $result.receipts[0].response.executed -ne $false -or
        $result.receipts[0].response.authorizes_trading -ne $false -or
        $result.old_intent_republished -ne 0 -or $result.broker_calls -ne 0 -or
        $result.account_binding -ne $false) { throw 'result binding/non-execution mismatch' }
    Write-NewJson 'coordinator-result.json' ([ordered]@{
        status = 'success'; at = (Get-Date).ToString('o'); run_id = $run; mode = $mode
        boot_time = $boot.ToString('o'); qmt_pid = $qmt.Id; qmt_started_at = $qmt.StartTime.ToString('o')
        driver_exit = $driver.ExitCode; receipts = 1
        response_status = $result.receipts[0].response.status; reason = $result.receipts[0].response.reason
        executed = $false; authorizes_trading = $false; old_intent_republished = 0
        broker_calls = 0; account_binding = $false; account_calls = 0; trading_calls = 0
        automatic_start_only = ($mode -ceq 'reboot'); manual_start_fallback = $false
        model_scheduled_sha256 = $scheduledSha
    })
} catch {
    $failure = $_
    try {
        if ($null -ne $driver -and -not $driver.HasExited) { $driver.WaitForExit(320000) | Out-Null }
        if ($null -ne $driver -and $driver.HasExited) {
            [IO.File]::WriteAllText((Join-Path $root 'failed-driver-stdout.txt'), $stdoutTask.Result)
            [IO.File]::WriteAllText((Join-Path $root 'failed-driver-stderr.txt'), $stderrTask.Result)
        }
    } catch {}
    try {
        Write-NewJson 'coordinator-failed.json' ([ordered]@{
            status = 'failed'; at = (Get-Date).ToString('o'); stage = $stage
            error_type = $failure.Exception.GetType().Name; message = $failure.Exception.Message
            automatic_replay = $false
            send_gate_created = (Test-Path -LiteralPath (Join-Path $root 'first-go.json'))
            account_calls = 0; trading_calls = 0
        })
    } catch {}
    exit 1
} finally {
    if ($null -ne $view) { $view.Dispose() }
    if ($null -ne $mapping) { $mapping.Dispose() }
    if ($null -ne $driver) { $driver.Dispose() }
    if ($taskName) {
        try {
            $service = New-Object -ComObject Schedule.Service; $service.Connect()
            $service.GetFolder('\').GetTask($taskName).Enabled = $false
        } catch {}
    }
}
