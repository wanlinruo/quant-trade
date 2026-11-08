# Default-off #601 authorized-order session coordinator (reviewed, run-agnostic).
#
# Bound only through fixture.json and requested.json in its own audit root.
# Creates the private run-bound named mapping (current SID + SYSTEM only),
# publishes the coordinator marker at offset 6144 BEFORE the driver starts,
# then runs the driver, which executes the pre-authorized plan exactly once.
# This script never calls trading APIs, never retries and never replays.
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$trustedExe = 'C:\self\qsys-qmt-2.1.33.0\bin.x64\XtItClient.exe'
$mapping = $null; $view = $null; $driver = $null; $stage = 'guard'; $taskName = $null
$stdoutTask = $null; $stderrTask = $null

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

try {
    $fixture = Read-Json 'fixture.json'
    $run = [string]$fixture.first
    $taskName = [string]$fixture.session_task
    if ($run -cnotmatch '^[0-9a-f]{32}$') { throw 'fixture run binding invalid' }
    if ([string]$fixture.mode -cne 'authorized-order') { throw 'fixture mode invalid' }
    if ($taskName -cne ('qsys-issue601-order-session-' + $run.Substring(0, 8))) { throw 'fixture task binding invalid' }
    if ((Get-Process -Id $PID).SessionId -ne 1) { throw 'interactive session 1 required' }
    $acl = Get-Acl -LiteralPath $root
    if (-not $acl.AreAccessRulesProtected) { throw 'private audit root required' }
    $trustees = @($acl.Access | ForEach-Object { $_.IdentityReference.Value } | Sort-Object -Unique)
    if (($trustees -join '|') -ne 'BUILTIN\Administrators|NT AUTHORITY\SYSTEM') { throw 'audit trustees changed' }
    $request = Read-Json 'requested.json'
    if ($request.run_id -cne $run -or $request.mode -cne 'authorized-order' -or $request.authorized_order -ne $true) {
        throw 'authorization binding changed'
    }
    foreach ($property in $request.hashes.PSObject.Properties) {
        $path = Join-Path $root $property.Name
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $property.Value) {
            throw ('source changed: ' + $property.Name)
        }
    }
    $rows = @(Get-CimInstance Win32_Process -Filter "Name = 'XtItClient.exe'" |
        Where-Object { $_.ExecutablePath -eq $trustedExe -and $_.SessionId -eq 1 })
    if ($rows.Count -ne 1 -or [int]$rows[0].ProcessId -ne [int]$request.pre_start.qmt_pid) { throw 'trusted QMT identity changed' }
    if (Test-Path -LiteralPath (Join-Path $root 'session-started.json')) { throw 'session already started; no replay' }
    Write-NewJson 'session-started.json' ([ordered]@{
        at = (Get-Date).ToString('o'); pid = $PID; session_id = 1; run_id = $run; automatic_replay = $false
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
        ('Local\qsys_authorized_order_' + $run), 65536,
        [IO.MemoryMappedFiles.MemoryMappedFileAccess]::ReadWrite,
        [IO.MemoryMappedFiles.MemoryMappedFileOptions]::None, $security,
        [IO.HandleInheritability]::None)

    $stage = 'coordinator_marker'
    $marker = [Text.Encoding]::ASCII.GetBytes('QSYS_AUTHORIZED_ORDER_ONLY:' + $run)
    $view = $mapping.CreateViewAccessor(0, 65536)
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
    $driver.StartInfo.Arguments = '-B "' + $root + '\native-order.py"'
    $driver.StartInfo.WorkingDirectory = $root
    $driver.StartInfo.UseShellExecute = $false
    $driver.StartInfo.CreateNoWindow = $true
    $driver.StartInfo.RedirectStandardOutput = $true
    $driver.StartInfo.RedirectStandardError = $true
    $driver.StartInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    if (-not $driver.Start()) { throw 'driver did not start' }
    $stdoutTask = $driver.StandardOutput.ReadToEndAsync()
    $stderrTask = $driver.StandardError.ReadToEndAsync()
    Write-NewJson 'driver-launched.json' ([ordered]@{ at = (Get-Date).ToString('o'); pid = $driver.Id; run_id = $run })

    $stage = 'wait_driver'
    if (-not $driver.WaitForExit(1500000)) { throw 'driver not terminal; no replay' }
    $driver.WaitForExit()
    [IO.File]::WriteAllText((Join-Path $root 'driver-stdout.txt'), $stdoutTask.Result)
    [IO.File]::WriteAllText((Join-Path $root 'driver-stderr.txt'), $stderrTask.Result)
    Write-NewJson 'session-result.json' ([ordered]@{
        status = $(if ($driver.ExitCode -eq 0) { 'completed' } else { 'driver_nonzero' })
        at = (Get-Date).ToString('o'); run_id = $run; driver_exit = $driver.ExitCode; automatic_replay = $false
    })
} catch {
    $failure = $_
    try {
        if ($null -ne $driver -and -not $driver.HasExited) { $driver.WaitForExit(60000) | Out-Null }
        if ($null -ne $driver -and $driver.HasExited) {
            [IO.File]::WriteAllText((Join-Path $root 'failed-driver-stdout.txt'), $stdoutTask.Result)
            [IO.File]::WriteAllText((Join-Path $root 'failed-driver-stderr.txt'), $stderrTask.Result)
        }
    } catch {}
    try {
        Write-NewJson 'session-failed.json' ([ordered]@{
            status = 'failed'; at = (Get-Date).ToString('o'); stage = $stage
            error_type = $failure.Exception.GetType().Name; message = $failure.Exception.Message
            automatic_replay = $false
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
