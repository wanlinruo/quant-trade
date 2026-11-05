"""Windows专属诊断状态根：只新建私有夹具，不修订现有目录的权限。

PowerShell返回SID及数字访问掩码，避免解析本地化的icacls文本。无交易入口。
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess

_FILES = {'.qsys-async-validation', 'dispatch.sqlite3', 'dispatch.lock',
          'dispatch.sqlite3-journal', 'dispatch.sqlite3-wal', 'dispatch.sqlite3-shm'}
_SYSTEM = 'S-1-5-18'
_ADMINS = 'S-1-5-32-544'
_FULL = 0x1F01FF

_INSPECT = r'''
$identity=[System.Security.Principal.WindowsIdentity]::GetCurrent()
$item=Get-Item -LiteralPath $path -Force
$reparse=$false
$cursor=$item
while($null -ne $cursor){
  if(($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){$reparse=$true}
  if($cursor -is [IO.DirectoryInfo]){$cursor=$cursor.Parent}else{$cursor=$cursor.Directory}
}
$drive=New-Object IO.DriveInfo ([IO.Path]::GetPathRoot($path))
$acl=Get-Acl -LiteralPath $path
$rules=@($acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])|ForEach-Object{
  @{sid=$_.IdentityReference.Value;mask=[int64]$_.FileSystemRights;
    allow=($_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow);
    inherit_only=(($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0);
    inheritance=[int]$_.InheritanceFlags}
})
@{sid=$identity.User.Value;owner=$acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value;
  protected=[bool]$acl.AreAccessRulesProtected;rules=$rules;reparse=$reparse;
  filesystem=$drive.DriveFormat;fixed=($drive.DriveType -eq [IO.DriveType]::Fixed);
  directory=[bool]$item.PSIsContainer}|ConvertTo-Json -Depth 5 -Compress
'''

_PROTECT_NEW_ROOT = r'''
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl=New-Object Security.AccessControl.DirectorySecurity
$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true,$false)
foreach($who in @($sid,(New-Object System.Security.Principal.SecurityIdentifier 'S-1-5-18'))){
  $rule=New-Object Security.AccessControl.FileSystemAccessRule($who,'FullControl',
    'ContainerInherit,ObjectInherit','None','Allow')
  $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $path -AclObject $acl
@{prepared=$true}|ConvertTo-Json -Compress
'''


def _powershell(path: Path, script: str) -> dict:
    # The path is data, never PowerShell source or a shell argument fragment.
    encoded_path = base64.b64encode(str(path).encode('utf-8')).decode('ascii')
    code = ("$ErrorActionPreference='Stop';[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false);"
            "$path=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
            + encoded_path + "'));" + script)
    executable = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-EncodedCommand',
            base64.b64encode(code.encode('utf-16le')).decode('ascii')],
            capture_output=True, timeout=15, check=True)
        value = json.loads(result.stdout.decode('utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('invalid security receipt')
        return value
    except (subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        # stderr may contain private paths or identities; do not embed it.
        raise ValueError('Windows security inspection failed: ' + type(exc).__name__) from None


def _assert_private(value: dict, *, directory: bool, protected: bool) -> None:
    sid = value.get('sid')
    if not isinstance(sid, str) or not re.fullmatch(r'S-1-5-21-(?:\d+-){3}\d+', sid):
        raise ValueError('explicit local/domain Windows identity required')
    if (value.get('filesystem') != 'NTFS' or value.get('fixed') is not True
            or value.get('reparse') is not False or value.get('directory') is not directory):
        raise ValueError('fixed local NTFS object without reparse ancestors required')
    if value.get('owner') not in {sid, _SYSTEM, _ADMINS}:
        raise ValueError('untrusted Windows object owner')
    if protected and value.get('protected') is not True:
        raise ValueError('protected private NTFS directory required')
    rules = value.get('rules')
    if not isinstance(rules, list) or len(rules) != 2:
        raise ValueError('exact private NTFS access list required')
    if {r.get('sid') for r in rules} != {sid, _SYSTEM}:
        raise ValueError('unexpected NTFS trustee')
    for rule in rules:
        if (rule.get('allow') is not True or rule.get('mask') != _FULL
                or rule.get('inherit_only') is not False
                or (directory and rule.get('inheritance') != 3)):
            raise ValueError('private NTFS access mask/inheritance mismatch')


def _local_path(root: Path) -> None:
    if os.name != 'nt':
        raise ValueError('Windows validation roots require Windows')
    if (not root.is_absolute() or not re.fullmatch(r'[A-Za-z]:', root.drive)
            or not re.fullmatch(r'qsys-async-validation-[a-z0-9_-]+', root.name)):
        raise ValueError('dedicated local Windows validation root required')


def validate_windows_validation_root(root: Path) -> dict:
    _local_path(root)
    receipt = _powershell(root, _INSPECT)
    _assert_private(receipt, directory=True, protected=True)
    if receipt['owner'] != receipt['sid']:
        raise ValueError('validation root must be owned by current Windows identity')
    for path in root.iterdir():
        if path.name not in _FILES:
            raise ValueError('unexpected file in Windows validation state root')
        _assert_private(_powershell(path, _INSPECT), directory=False, protected=False)
        if path.stat().st_nlink != 1:
            raise ValueError('Windows validation state hardlink rejected')
    required = {'.qsys-async-validation', 'dispatch.sqlite3', 'dispatch.lock'}
    if not required <= {p.name for p in root.iterdir()}:
        raise ValueError('Windows validation root must be prepared before activation')
    return receipt


def prepare_windows_validation_root(root: Path, namespace: str, *, enabled: bool = False) -> dict:
    if enabled is not True:
        raise ValueError('Windows validation preparation is disabled')
    _local_path(root)
    if not re.fullmatch(r'component-validation:[a-z0-9][a-z0-9:_-]{0,95}', namespace):
        raise ValueError('explicit isolated namespace required')
    # Inherit a private DACL from the first instant. Never change the parent ACL.
    _assert_private(_powershell(root.parent, _INSPECT), directory=True, protected=True)
    root.mkdir(exist_ok=False)
    # A failure preserves the new directory for audit; no overwrite or cleanup retry.
    if _powershell(root, _PROTECT_NEW_ROOT).get('prepared') is not True:
        raise ValueError('Windows validation root preparation incomplete')
    for name, content in (('.qsys-async-validation', namespace),
                          ('dispatch.sqlite3', ''), ('dispatch.lock', '0')):
        with (root / name).open('x', encoding='utf-8', newline='') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    return validate_windows_validation_root(root)
