# #599: ephemeral diagnostic peer only, never a production listener.
param([Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{32}$')][string]$RunId,
      [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{32}$')][string]$RequestId,
      [Parameter(Mandatory=$true)][string]$AuditRoot)
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
$mapping=$null;$view=$null;$stage='guard'
try {
 if($AuditRoot -notmatch '^C:\\qsys\\var-deployments\\issue599-ui-[0-9]{8}-m[0-9]+$'){throw 'Diagnostic root required'}
 if((Get-Process -Id $PID).SessionId -ne 1){throw 'Expected interactive session'}
 $protocol='qsys.external-mmap-diagnostic.v1';$payload='QSYS599_DIAGNOSTIC_ONLY'
 Add-Type -AssemblyName System.Core
 $security=New-Object IO.MemoryMappedFiles.MemoryMappedFileSecurity
 $security.SetAccessRuleProtection($true,$false)
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
 foreach($allowed in @($sid,(New-Object Security.Principal.SecurityIdentifier('S-1-5-18')))){
  $rule=$security.AccessRuleFactory($allowed,[int][IO.MemoryMappedFiles.MemoryMappedFileRights]::FullControl,$false,[Security.AccessControl.InheritanceFlags]::None,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
  $security.AddAccessRule($rule)
 }
 $stage='create_memory'
 $mapping=[IO.MemoryMappedFiles.MemoryMappedFile]::CreateNew(('Local\qsys599_diag_'+$RunId),4096,[IO.MemoryMappedFiles.MemoryMappedFileAccess]::ReadWrite,[IO.MemoryMappedFiles.MemoryMappedFileOptions]::None,$security,[IO.HandleInheritability]::None)
 $view=$mapping.CreateViewStream(0,4096,[IO.MemoryMappedFiles.MemoryMappedFileAccess]::ReadWrite)
 $issued=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds();$expires=$issued+120000
 $request=@{protocol=$protocol;run_id=$RunId;request_id=$RequestId;kind='ping';payload=$payload;issued_at_ms=$issued;expires_at_ms=$expires}|ConvertTo-Json -Compress
 $bytes=[Text.Encoding]::UTF8.GetBytes($request)
 if($bytes.Length -gt 1000){throw 'Diagnostic frame limit'}
 $header=[BitConverter]::GetBytes([int]$bytes.Length)
 $view.Write($header,0,4);$view.Write($bytes,0,$bytes.Length);$view.Flush()
 @{at=(Get-Date).ToString('o');pid=$PID;session=1;run_id=$RunId;request_id=$RequestId;request_bytes=$bytes.Length;expires_at_ms=$expires;trading_calls=0;account_calls=0}|ConvertTo-Json|Set-Content ($AuditRoot+'\memory-ready.json') -Encoding UTF8
 $stage='await_diagnostic_echo';$timer=[Diagnostics.Stopwatch]::StartNew();$received=$false
 while($timer.ElapsedMilliseconds -lt 125000){
  $view.Position=1024;$head=New-Object byte[] 4
  if($view.Read($head,0,4) -ne 4){throw 'Short response header'}
  $length=[BitConverter]::ToInt32($head,0)
  if($length -eq 0){Start-Sleep -Milliseconds 100;continue}
  if($length -lt 1 -or $length -gt 1000){throw 'Invalid response size'}
  $response=New-Object byte[] $length
  if($view.Read($response,0,$length) -ne $length){throw 'Short response'}
  $obj=[Text.Encoding]::UTF8.GetString($response)|ConvertFrom-Json
  if(@($obj.PSObject.Properties).Count -ne 6 -or $obj.protocol -ne $protocol -or $obj.run_id -ne $RunId -or $obj.request_id -ne $RequestId -or $obj.kind -ne 'echo' -or $obj.payload -cne $payload -or $obj.authorizes_trading -isnot [bool] -or $obj.authorizes_trading){throw 'Unexpected diagnostic response'}
  $received=$true;break
 }
 if(-not $received){throw 'Bounded diagnostic response timeout'}
 $stage='receipt';$sha=[Security.Cryptography.SHA256]::Create()
 try{$responseSHA=([BitConverter]::ToString($sha.ComputeHash($response))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 @{status='success';protocol=$protocol;at=(Get-Date).ToString('o');run_id=$RunId;request_id=$RequestId;server_pid=$PID;session=1;request_bytes=$bytes.Length;response_bytes=$length;response_sha256=$responseSHA;payload_exact=$true;elapsed_ms=$timer.ElapsedMilliseconds;application_auth_verified=$false;runtime_peer_identity_verified=$false;broker_permission_verified=$false;authorizes_trading=$false;account_calls=0;trading_calls=0}|ConvertTo-Json|Set-Content ($AuditRoot+'\memory-result.json') -Encoding UTF8
} catch {
 @{status='failed';at=(Get-Date).ToString('o');run_id=$RunId;stage=$stage;error_type=$_.Exception.GetType().Name;automatic_replay=$false;trading_calls=0}|ConvertTo-Json|Set-Content ($AuditRoot+'\memory-failed.json') -Encoding UTF8
 throw
} finally {if($null -ne $view){$view.Dispose()};if($null -ne $mapping){$mapping.Dispose()}}
