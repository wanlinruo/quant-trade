# #599 diagnostic only. Fixed PUBLIC fixture key, never production identity.
param([Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{32}$')][string]$RunId,
      [Parameter(Mandatory=$true)][string]$AuditRoot)
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
$mapping=$null;$view=$null;$stage='guard'
# Public fixture: 32 bytes of decimal 17. No production secret is accepted.
$key=New-Object byte[] 32;for($n=0;$n -lt 32;$n++){$key[$n]=17}
function GetQsysMac($Value) {
 $body=[Text.Encoding]::ASCII.GetBytes(($Value|ConvertTo-Json -Compress))
 $h=[Security.Cryptography.HMACSHA256]::new([byte[]]$key)
 try{([BitConverter]::ToString($h.ComputeHash($body))).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()}
}
function NewQsysRequest([int]$Sequence,[string]$Id,[long]$Issued,[long]$Expires) {
 $obj=[ordered]@{expires_at_ms=$Expires;issued_at_ms=$Issued;kind='ping';payload='QSYS599_DIAGNOSTIC_ONLY';protocol='qsys.external-queue-diagnostic.v1';request_id=$Id;run_id=$RunId;seq=$Sequence}
 $mac=GetQsysMac $obj;$obj['mac']=$mac;return $obj
}
try {
 if($AuditRoot -notmatch '^C:\\qsys\\var-deployments\\issue599-ui-[0-9]{8}-q[0-9]+$'){throw 'Diagnostic audit root required'}
 if((Get-Process -Id $PID).SessionId -ne 1){throw 'Session mismatch'}
 $acl=Get-Acl -LiteralPath $AuditRoot
 if(-not $acl.AreAccessRulesProtected -or $acl.Access.Count -ne 2){throw 'Private audit ACL required'}
 Add-Type -AssemblyName System.Core
 $security=New-Object IO.MemoryMappedFiles.MemoryMappedFileSecurity
 $security.SetAccessRuleProtection($true,$false);$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
 foreach($who in @($sid,(New-Object Security.Principal.SecurityIdentifier('S-1-5-18')))){
  $rule=$security.AccessRuleFactory($who,[int][IO.MemoryMappedFiles.MemoryMappedFileRights]::FullControl,$false,[Security.AccessControl.InheritanceFlags]::None,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow);$security.AddAccessRule($rule)
 }
 $stage='create_memory'
 $mapping=[IO.MemoryMappedFiles.MemoryMappedFile]::CreateNew(('Local\qsys599_queue_'+$RunId),8192,[IO.MemoryMappedFiles.MemoryMappedFileAccess]::ReadWrite,[IO.MemoryMappedFiles.MemoryMappedFileOptions]::None,$security,[IO.HandleInheritability]::None)
 $view=$mapping.CreateViewStream(0,8192,[IO.MemoryMappedFiles.MemoryMappedFileAccess]::ReadWrite)
 $issued=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds();$expires=$issued+120000
 $a=[Guid]::NewGuid().ToString('N');$b=[Guid]::NewGuid().ToString('N');$c=[Guid]::NewGuid().ToString('N');$d=[Guid]::NewGuid().ToString('N')
 $first=NewQsysRequest 1 $a $issued $expires
 $bad=NewQsysRequest 2 $b $issued $expires;$bad['mac']='0'*64
 $fixtures=@($first,$first,$bad,(NewQsysRequest 3 $c $issued $expires),(NewQsysRequest 2 $b ($issued-3000) ($issued-1000)),(NewQsysRequest 2 $b $issued $expires),(NewQsysRequest 2 $b ($issued+1) ($expires+1)),(NewQsysRequest 3 $d $issued $expires))
 $reasons=@('ok','ok','authentication','sequence','expired','ok','request_conflict','ok');$counts=@(1,1,1,1,1,2,2,3)
 @{at=(Get-Date).ToString('o');run_id=$RunId;pid=$PID;session=1;expires_at_ms=$expires;public_fixture_key=$true;account_calls=0;trading_calls=0}|ConvertTo-Json|Set-Content ($AuditRoot+'\queue-ready.json') -Encoding UTF8
 $clock=[Diagnostics.Stopwatch]::StartNew();$rows=@();$firstReply=$null
 for($index=0;$index -lt 8;$index++) {
  $stage='publish_'+($index+1);$generation=$index+1
  $bytes=[Text.Encoding]::ASCII.GetBytes(($fixtures[$index]|ConvertTo-Json -Compress))
  if($bytes.Length -gt 2000){throw 'Request limit'}
  $view.Position=0;$view.Write((New-Object byte[] 4),0,4)
  $view.Position=8;$view.Write($bytes,0,$bytes.Length)
  $view.Position=4;$view.Write([BitConverter]::GetBytes([int]$bytes.Length),0,4)
  $view.Position=0;$view.Write([BitConverter]::GetBytes([int]$generation),0,4);$view.Flush()
  $stage='await_'+$generation;$response=$null
  while($clock.ElapsedMilliseconds -lt 125000) {
   $view.Position=4096;$head=New-Object byte[] 4;if($view.Read($head,0,4) -ne 4){throw 'Short generation'}
   if([BitConverter]::ToInt32($head,0) -ne $generation){Start-Sleep -Milliseconds 50;continue}
   $lenBytes=New-Object byte[] 4;if($view.Read($lenBytes,0,4) -ne 4){throw 'Short length'};$length=[BitConverter]::ToInt32($lenBytes,0)
   if($length -lt 1 -or $length -gt 2000){throw 'Response limit'}
   $response=New-Object byte[] $length;if($view.Read($response,0,$length) -ne $length){throw 'Short body'}
   $view.Position=4096;if($view.Read($head,0,4) -ne 4 -or [BitConverter]::ToInt32($head,0) -ne $generation){throw 'Unstable response'}
   break
  }
  if($null -eq $response){throw 'Bounded response timeout'}
  $stage='verify_'+$generation;$obj=[Text.Encoding]::ASCII.GetString($response)|ConvertFrom-Json
  if(@($obj.PSObject.Properties).Count -ne 9 -or $obj.protocol -cne 'qsys.external-queue-diagnostic.v1' -or $obj.run_id -cne $RunId -or $obj.request_id -cne $fixtures[$index]['request_id'] -or $obj.seq -ne $fixtures[$index]['seq'] -or $obj.reason -cne $reasons[$index] -or $obj.accepted_count -ne $counts[$index] -or $obj.authorizes_trading -isnot [bool] -or $obj.authorizes_trading){throw 'Unexpected diagnostic response'}
  if(($obj.status -ceq 'echo') -ne ($obj.reason -ceq 'ok')){throw 'Response status'}
  $unsigned=[ordered]@{accepted_count=$obj.accepted_count;authorizes_trading=$false;protocol=$obj.protocol;reason=$obj.reason;request_id=$obj.request_id;run_id=$obj.run_id;seq=$obj.seq;status=$obj.status}
  if($obj.mac -cne (GetQsysMac $unsigned)){throw 'Response HMAC mismatch'}
  $responseText=[Convert]::ToBase64String($response)
  if($index -eq 0){$firstReply=$responseText};if($index -eq 1 -and $firstReply -cne $responseText){throw 'Duplicate reply changed'}
  $sha=[Security.Cryptography.SHA256]::Create();try{$digest=([BitConverter]::ToString($sha.ComputeHash($response))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  $rows+=@{generation=$generation;request_id=$obj.request_id;seq=$obj.seq;reason=$obj.reason;accepted_count=$obj.accepted_count;request_bytes=$bytes.Length;response_bytes=$length;response_sha256=$digest;response_mac_verified=$true}
 }
 @{status='success';at=(Get-Date).ToString('o');run_id=$RunId;server_pid=$PID;session=1;frames=$rows;accepted_unique=3;duplicate_reply_identical=$true;public_fixture_key=$true;production_identity_verified=$false;durable_dedup_verified=$false;broker_permission_verified=$false;authorizes_trading=$false;account_calls=0;trading_calls=0}|ConvertTo-Json -Depth 6|Set-Content ($AuditRoot+'\queue-result.json') -Encoding UTF8
} catch {
 @{status='failed';at=(Get-Date).ToString('o');run_id=$RunId;stage=$stage;error_type=$_.Exception.GetType().Name;automatic_replay=$false;trading_calls=0}|ConvertTo-Json|Set-Content ($AuditRoot+'\queue-failed.json') -Encoding UTF8
 throw
} finally {if($null -ne $view){$view.Dispose()};if($null -ne $mapping){$mapping.Dispose()}}
