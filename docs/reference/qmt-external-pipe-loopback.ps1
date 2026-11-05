# #599: TWO EXTERNAL TEST PROCESSES ONLY. Never load this into QMT.
# Byte echo on an ephemeral owner/SYSTEM pipe; no broker or market interface.
param(
 [Parameter(Mandatory=$true)][ValidateSet('server','client')][string]$Role,
 [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{32}$')][string]$RunId,
 [Parameter(Mandatory=$true)][string]$AuditRoot,
 [int]$ExpectedServerPid=0
)
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
$pipe=$null;$watch=[Diagnostics.Stopwatch]::StartNew();$stage='validate'
function GetQsysBytesHash([byte[]]$Bytes) {
 $hash=[Security.Cryptography.SHA256]::Create()
 try {([BitConverter]::ToString($hash.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()}
 finally {$hash.Dispose()}
}
function WaitQsysIO($Operation) {
 $remaining=15000-$watch.ElapsedMilliseconds
 if($remaining -le 0 -or -not $Operation.AsyncWaitHandle.WaitOne([int]$remaining)){throw 'Bounded test I/O timed out'}
}
function ReadQsysBytes([int]$Count) {
 $buffer=New-Object byte[] $Count;$offset=0
 while($offset -lt $Count) {
  $op=$pipe.BeginRead($buffer,$offset,$Count-$offset,$null,$null)
  try {WaitQsysIO $op;$got=$pipe.EndRead($op)} finally {$op.AsyncWaitHandle.Close()}
  if($got -eq 0){throw 'Unexpected pipe EOF'}
  $offset+=$got
 }
 return ,$buffer
}
function ReadQsysFrame {
 $header=ReadQsysBytes 4;$size=[BitConverter]::ToInt32($header,0)
 if($size -lt 1 -or $size -gt 4096){throw 'Frame outside diagnostic limit'}
 return ,(ReadQsysBytes $size)
}
function WriteQsysBytes([byte[]]$Bytes) {
 $op=$pipe.BeginWrite($Bytes,0,$Bytes.Length,$null,$null)
 try {WaitQsysIO $op;$pipe.EndWrite($op)} finally {$op.AsyncWaitHandle.Close()}
}
function WriteQsysFrame([byte[]]$Bytes) {
 if($Bytes.Length -lt 1 -or $Bytes.Length -gt 4096){throw 'Invalid diagnostic frame'}
 WriteQsysBytes ([BitConverter]::GetBytes([int]$Bytes.Length));WriteQsysBytes $Bytes
}
try {
 if($AuditRoot -notmatch '^C:\\qsys\\var-deployments\\issue599-pipe-loopback-[0-9]{8}-r[0-9]+$'){throw 'Private audit root required'}
 if(-not [BitConverter]::IsLittleEndian){throw 'Unexpected test encoding'}
 $fixture=Get-Content -LiteralPath ($AuditRoot+'\frames.json') -Raw -Encoding UTF8|ConvertFrom-Json
 if($fixture.protocol -ne 'qsys.external-byte-echo.v1' -or $fixture.run_id -ne $RunId -or $fixture.frames.Count -lt 1 -or $fixture.frames.Count -gt 8){throw 'Invalid test fixture'}
 $name='qsys599_test_'+$RunId
 Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class Qsys599PipePeer {
 [DllImport("kernel32.dll",SetLastError=true)] public static extern bool GetNamedPipeServerProcessId(IntPtr p,out uint id);
 [DllImport("kernel32.dll",SetLastError=true)] public static extern bool GetNamedPipeClientProcessId(IntPtr p,out uint id);
}
'@
 $stage='connect';$aclHash=$null
 if($Role -eq 'server') {
  $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
  $security=New-Object IO.Pipes.PipeSecurity
  $security.SetAccessRuleProtection($true,$false)
  $security.AddAccessRule((New-Object IO.Pipes.PipeAccessRule($sid,[IO.Pipes.PipeAccessRights]::FullControl,[Security.AccessControl.AccessControlType]::Allow)))
  $systemSid=New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
  $security.AddAccessRule((New-Object IO.Pipes.PipeAccessRule($systemSid,[IO.Pipes.PipeAccessRights]::FullControl,[Security.AccessControl.AccessControlType]::Allow)))
  $sddl=$security.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access)
  $aclHash=GetQsysBytesHash ([Text.Encoding]::UTF8.GetBytes($sddl))
  $pipe=New-Object IO.Pipes.NamedPipeServerStream($name,[IO.Pipes.PipeDirection]::InOut,1,[IO.Pipes.PipeTransmissionMode]::Byte,[IO.Pipes.PipeOptions]::Asynchronous,4096,4096,$security)
  @{pid=$PID;run_id=$RunId;acl_sha256=$aclHash}|ConvertTo-Json|Set-Content -LiteralPath ($AuditRoot+'\server-ready.json') -Encoding UTF8
  $op=$pipe.BeginWaitForConnection($null,$null)
  try {WaitQsysIO $op;$pipe.EndWaitForConnection($op)} finally {$op.AsyncWaitHandle.Close()}
  [uint32]$peerId=0
  if(-not [Qsys599PipePeer]::GetNamedPipeClientProcessId($pipe.SafePipeHandle.DangerousGetHandle(),[ref]$peerId)){throw 'Client PID query failed'}
 } else {
  if($ExpectedServerPid -le 0){throw 'Explicit server PID required'}
  $pipe=New-Object IO.Pipes.NamedPipeClientStream('.',$name,[IO.Pipes.PipeDirection]::InOut,[IO.Pipes.PipeOptions]::Asynchronous,[Security.Principal.TokenImpersonationLevel]::Identification)
  $pipe.Connect(5000)
  [uint32]$peerId=0
  if(-not [Qsys599PipePeer]::GetNamedPipeServerProcessId($pipe.SafePipeHandle.DangerousGetHandle(),[ref]$peerId) -or $peerId -ne $ExpectedServerPid){throw 'Server PID mismatch'}
 }
 $stage='echo';$rows=@();$sequence=0
 foreach($frame in $fixture.frames) {
  $expected=[Convert]::FromBase64String($frame.base64)
  if((GetQsysBytesHash $expected) -ne $frame.sha256 -or $expected.Length -gt 4096){throw 'Fixture SHA mismatch'}
  $timer=[Diagnostics.Stopwatch]::StartNew()
  if($Role -eq 'client'){WriteQsysFrame $expected;$received=ReadQsysFrame}
  else {$received=ReadQsysFrame;if((GetQsysBytesHash $received) -ne $frame.sha256){throw 'Unexpected test bytes'};WriteQsysFrame $received}
  if((GetQsysBytesHash $received) -ne $frame.sha256 -or $received.Length -ne $expected.Length){throw 'Byte echo mismatch'}
  $sequence++;$rows+=@{seq=$sequence;bytes=$received.Length;sha256=$frame.sha256;elapsed_ms=$timer.Elapsed.TotalMilliseconds}
 }
 $pipe.Dispose();$pipe=$null
 @{status='success';protocol=$fixture.protocol;run_id=$RunId;role=$Role;pid=$PID;peer_pid=$peerId;acl_sha256=$aclHash;frames=$rows;elapsed_ms=$watch.ElapsedMilliseconds;account_calls=0;trading_calls=0;qmt_processes_used=0;qmt_channel_verified=$false;application_auth_verified=$false;authorizes_trading=$false}|ConvertTo-Json -Depth 6|Set-Content -LiteralPath ($AuditRoot+'\'+$Role+'-result.json') -Encoding UTF8
} catch {
 @{status='failed';role=$Role;stage=$stage;error_type=$_.Exception.GetType().Name;pid=$PID;automatic_replay=$false;trading_calls=0;qmt_processes_used=0}|ConvertTo-Json|Set-Content -LiteralPath ($AuditRoot+'\'+$Role+'-failed.json') -Encoding UTF8
 exit 1
} finally {if($null -ne $pipe){$pipe.Dispose()}}
