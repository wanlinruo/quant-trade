# #599: private diagnostic workers only; never touches broker/business tasks.
param([Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{32}$')][string]$ExerciseId,
 [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{32}$')][string]$SessionId,
 [Parameter(Mandatory=$true)][ValidateSet('first','second')][string]$Phase,
 [Parameter(Mandatory=$true)][string]$MessagesBase64,
 [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$LedgerSHA,
 [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$GatewaySHA,
 [Parameter(Mandatory=$true)][string]$AuditRoot)
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
$mapping=$null;$view=$null;$lock=$null;$driver=$null;$stage='guard'
try {
 if($AuditRoot -notmatch '^C:\\qsys\\var-deployments\\issue599-ui-20261105-q[0-9]+$'){throw 'Diagnostic root required'}
 if((Get-Process -Id $PID).SessionId -ne 1){throw 'Interactive session required'}
 $private='C:\qsys\var-deployments\issue599-private-20261105';$acl=Get-Acl $private
 if(-not $acl.AreAccessRulesProtected -or $acl.Access.Count -ne 2){throw 'Private staging required'}
 $ledgerRoot=$private+'\qsys599-ledger-'+$ExerciseId
 $stage='fixture'
 if($Phase -eq 'first'){
  if(Test-Path $ledgerRoot){throw 'Fixture exists; reconcile, do not replay'}
  New-Item -ItemType Directory -Path $ledgerRoot|Out-Null;Set-Acl -LiteralPath $ledgerRoot -AclObject $acl
 }elseif(-not (Test-Path -LiteralPath $ledgerRoot -PathType Container)){throw 'Prior fixture missing'}
 $stage='exclusive_diagnostic_lock'
 $lock=[IO.File]::Open(($ledgerRoot+'\coordinator.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
 if($Phase -eq 'first'){[IO.File]::WriteAllText(($ledgerRoot+'\.diagnostic-only'),$ExerciseId,[Text.Encoding]::ASCII)}
 elseif([IO.File]::ReadAllText(($ledgerRoot+'\.diagnostic-only')) -cne $ExerciseId){throw 'Fixture marker changed'}
 if($Phase -eq 'second'){
  $prior=Get-Content -Raw -Encoding UTF8 ($ledgerRoot+'\phase-first-complete.json')|ConvertFrom-Json
  if($prior.exercise_id -cne $ExerciseId -or $prior.session_id -ceq $SessionId -or $prior.driver_exit -ne 0){throw 'Prior phase not proven terminal'}
 }
 $phaseMarker=$ledgerRoot+'\phase-'+$Phase+'-requested.json'
 $markerBytes=[Text.Encoding]::UTF8.GetBytes((@{exercise_id=$ExerciseId;session_id=$SessionId;phase=$Phase;at=(Get-Date).ToString('o')}|ConvertTo-Json -Compress))
 $markerStream=[IO.File]::Open($phaseMarker,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$markerStream.Write($markerBytes,0,$markerBytes.Length);$markerStream.Flush($true)}finally{$markerStream.Dispose()}
 $stage='source'
 foreach($item in @(@('qmt-external-dispatch-ledger.py',$LedgerSHA),@('qmt-external-recovery-gateway.py',$GatewaySHA))){
  $source=$private+'\artifact-'+$item[1]+'.py';$target=$AuditRoot+'\'+$item[0]
  if((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -cne $item[1]){throw 'Diagnostic source hash changed'}
  if(Test-Path $target){throw 'Destination exists; no replay'}
  [IO.File]::WriteAllBytes($target,[IO.File]::ReadAllBytes($source))
 }
 $messages=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($MessagesBase64))|ConvertFrom-Json
 if(@($messages.PSObject.Properties).Count -ne 5){throw 'Message IDs missing'}
 foreach($name in @('a','b','c','d','e')){if($messages.$name -notmatch '^[0-9a-f]{32}$'){throw 'Invalid diagnostic ID'}}
 $issued=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds();$expires=$issued+120000
 @{exercise_id=$ExerciseId;session_id=$SessionId;phase=$Phase;messages=$messages;issued_ms=$issued;expires_ms=$expires;ledger_root=$ledgerRoot;ledger_source_sha256=$LedgerSHA}|ConvertTo-Json -Depth 4|Set-Content -LiteralPath ($AuditRoot+'\fixture.json') -Encoding UTF8
 Add-Type -AssemblyName System.Core
 $security=New-Object IO.MemoryMappedFiles.MemoryMappedFileSecurity;$security.SetAccessRuleProtection($true,$false)
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
 foreach($who in @($sid,(New-Object Security.Principal.SecurityIdentifier('S-1-5-18')))){
  $rule=$security.AccessRuleFactory($who,[int][IO.MemoryMappedFiles.MemoryMappedFileRights]::FullControl,$false,[Security.AccessControl.InheritanceFlags]::None,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow);$security.AddAccessRule($rule)
 }
 $stage='create_mapping'
 $mapping=[IO.MemoryMappedFiles.MemoryMappedFile]::CreateNew(('Local\qsys599_queue_'+$SessionId),8192,[IO.MemoryMappedFiles.MemoryMappedFileAccess]::ReadWrite,[IO.MemoryMappedFiles.MemoryMappedFileOptions]::None,$security,[IO.HandleInheritability]::None)
 $view=$mapping.CreateViewStream(0,8192,[IO.MemoryMappedFiles.MemoryMappedFileAccess]::ReadWrite)
 $marker=[Text.Encoding]::ASCII.GetBytes('QSYS599_RECOVERY_ONLY:'+$SessionId);$view.Position=6144;$view.Write($marker,0,$marker.Length);$view.Flush()
 $stage='start_diagnostic_driver'
 $driver=New-Object Diagnostics.Process
 $driver.StartInfo.FileName='C:\self\qsys-qmt-2.1.33.0\bin.x64\python312\python.exe'
 $driver.StartInfo.Arguments='-B "'+$AuditRoot+'\qmt-external-recovery-gateway.py" phase "'+$AuditRoot+'"'
 $driver.StartInfo.WorkingDirectory=$AuditRoot;$driver.StartInfo.UseShellExecute=$false;$driver.StartInfo.CreateNoWindow=$true
 $driver.StartInfo.RedirectStandardOutput=$true;$driver.StartInfo.RedirectStandardError=$true
 $driver.StartInfo.EnvironmentVariables['PYTHONIOENCODING']='utf-8';$driver.StartInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE']='1'
 if(-not $driver.Start()){throw 'Diagnostic driver did not start'}
 @{at=(Get-Date).ToString('o');exercise_id=$ExerciseId;session_id=$SessionId;phase=$Phase;expires_at_ms=$expires;pid=$PID;driver_pid=$driver.Id;session=1;exclusive_fixture_lock=$true;account_calls=0;trading_calls=0}|ConvertTo-Json|Set-Content ($AuditRoot+'\recovery-ready.json') -Encoding UTF8
 $stage='wait_driver'
 if(-not $driver.WaitForExit(125000)){throw 'Driver not terminal; inspect PID and receipts, do not replay'}
 $driver.Refresh();$exitCode=$driver.ExitCode
 [IO.File]::WriteAllText(($AuditRoot+'\driver-output-private.txt'),$driver.StandardOutput.ReadToEnd())
 [IO.File]::WriteAllText(($AuditRoot+'\driver-stderr-private.txt'),$driver.StandardError.ReadToEnd())
 @{at=(Get-Date).ToString('o');driver_pid=$driver.Id;exit_code=$exitCode;has_exited=$driver.HasExited}|ConvertTo-Json|Set-Content ($AuditRoot+'\driver-exit.json') -Encoding UTF8
 if($exitCode -ne 0){throw 'Diagnostic driver failed; preserve child receipts'}
 $stage='verify_receipt';$receipt=Get-Content -Raw -Encoding UTF8 ($AuditRoot+'\recovery-result.json')|ConvertFrom-Json
 if($receipt.status -cne 'success' -or $receipt.session_id -cne $SessionId -or $receipt.phase -cne $Phase){throw 'Diagnostic result mismatch'}
 $completed=[Text.Encoding]::UTF8.GetBytes((@{exercise_id=$ExerciseId;session_id=$SessionId;phase=$Phase;driver_exit=$exitCode;at=(Get-Date).ToString('o')}|ConvertTo-Json -Compress))
 $completedStream=[IO.File]::Open(($ledgerRoot+'\phase-'+$Phase+'-complete.json'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$completedStream.Write($completed,0,$completed.Length);$completedStream.Flush($true)}finally{$completedStream.Dispose()}
 @{status='success';at=(Get-Date).ToString('o');phase=$Phase;session_id=$SessionId;exercise_id=$ExerciseId;driver_exit=$exitCode;driver_pid=$driver.Id;account_calls=0;trading_calls=0;authorizes_trading=$false}|ConvertTo-Json|Set-Content ($AuditRoot+'\coordinator-result.json') -Encoding UTF8
} catch {
 @{status='failed';at=(Get-Date).ToString('o');phase=$Phase;exercise_id=$ExerciseId;session_id=$SessionId;stage=$stage;error_type=$_.Exception.GetType().Name;automatic_replay=$false;trading_calls=0}|ConvertTo-Json|Set-Content ($AuditRoot+'\coordinator-failed.json') -Encoding UTF8
 throw
} finally {
 if($null -ne $view){$view.Dispose()};if($null -ne $mapping){$mapping.Dispose()};if($null -ne $lock){$lock.Dispose()}
 if($null -ne $driver){$driver.Dispose()} # Disposes this handle; never kills a process.
}
