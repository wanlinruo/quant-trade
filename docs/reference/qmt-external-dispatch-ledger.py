"""#599 OFFLINE diagnostic ledger, never imported by the trading application.

Only a marked private fixture directory and fixed synthetic payload are accepted.
Transport ACK is not broker acceptance. Unknown attempts cannot be claimed again.
No transport, callback, broker, account or real order interface exists here.
"""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess


def identifier(value, size=32):
    return isinstance(value, str) and len(value) == size and all(
        c in '0123456789abcdef' for c in value)


def windows_private_directory(root):
    """Verify the actual NTFS DACL; POSIX chmod bits do not prove it on Windows."""
    encoded = base64.b64encode(str(root).encode('utf-8')).decode('ascii')
    script = r'''$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
try {
 $p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PATH_BASE64'))
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
 $item=Get-Item -LiteralPath $p -Force
 $ancestor=$item
 while($null -ne $ancestor){if(($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Reparse path'};$ancestor=$ancestor.Parent}
 $acl=Get-Acl -LiteralPath $p
 $allows=@($acl.Access|Where-Object{$_.AccessControlType -eq 'Allow'})
 if(-not $acl.AreAccessRulesProtected -or $allows.Count -ne 2){throw 'Private root required'}
 $allowed=@($sid,'S-1-5-18')
 $seen=@{};foreach($rule in $allows){$who=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value;if($who -notin $allowed -or ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne [Security.AccessControl.FileSystemRights]::FullControl){throw 'Unexpected principal or rights'};$seen[$who]=$true};if($seen.Count -ne 2){throw 'Missing private principal'}
 $db=Join-Path $p 'ledger.sqlite3'
 if(Test-Path -LiteralPath $db){
  $f=Get-Item -LiteralPath $db -Force
  if(($f.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Reparse database'}
  foreach($rule in (Get-Acl -LiteralPath $db).Access){if($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowed){throw 'Unexpected database principal'}}
 }
 [Console]::Write('PRIVATE')
}catch{[Console]::Write('REJECTED');exit 1}
'''.replace('PATH_BASE64', encoded)
    try:
        result = subprocess.run([
            r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
            '-NoProfile', '-NonInteractive', '-EncodedCommand',
            base64.b64encode(script.encode('utf-16le')).decode('ascii')],
            capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('NTFS fixture validation unavailable') from None
    if result.returncode != 0 or result.stdout != b'PRIVATE':
        raise ValueError('NTFS private fixture rejected')


class DiagnosticLedger:
    def __init__(self, root, run_id):
        root = Path(root)
        if not identifier(run_id) or root.name != 'qsys599-ledger-' + run_id:
            raise ValueError('explicit diagnostic fixture required')
        if root.is_symlink() or not root.is_dir():
            raise ValueError('private fixture directory required')
        if os.name == 'nt':
            windows_private_directory(root)
        elif stat.S_IMODE(root.stat().st_mode) & 0o077:
            raise ValueError('private fixture directory required')
        if (root / '.diagnostic-only').read_text() != run_id:
            raise ValueError('diagnostic marker mismatch')
        self.run_id = run_id
        self.path = root / 'ledger.sqlite3'
        if self.path.is_symlink():
            raise ValueError('symlink database rejected')
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        with self.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS diagnostic_messages (
                message_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL,
                issued_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
                state TEXT NOT NULL, attempt_id TEXT, session_nonce TEXT,
                transport_ack_sha256 TEXT)''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def prepare(self, message_id, issued_ms, expires_ms):
        if not identifier(message_id) or type(issued_ms) is not int or type(expires_ms) is not int:
            raise ValueError('diagnostic message binding')
        if not 0 <= issued_ms < expires_ms <= issued_ms + 120000:
            raise ValueError('diagnostic time window')
        value = dict(run_id=self.run_id, message_id=message_id, issued_ms=issued_ms,
                     expires_ms=expires_ms, payload='QSYS599_DIAGNOSTIC_ONLY')
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        with self.connection() as db:
            old = db.execute('SELECT payload_sha256 FROM diagnostic_messages WHERE message_id=?',
                             (message_id,)).fetchone()
            if old is not None:
                if old[0] != digest:
                    raise ValueError('same ID changed immutable message')
                return
            db.execute('INSERT INTO diagnostic_messages VALUES (?,?,?,?,?,?,?,?)',
                       (message_id, digest, issued_ms, expires_ms, 'prepared', None, None, None))

    def claim(self, message_id, attempt_id, session_nonce, now_ms):
        if not all(identifier(v) for v in (message_id, attempt_id, session_nonce)) or type(now_ms) is not int:
            raise ValueError('attempt binding')
        with self.connection() as db:
            # Commit this marker BEFORE any external effect. There is no
            # external effect in this diagnostic class, even after True.
            changed = db.execute('''UPDATE diagnostic_messages
                SET state='dispatching', attempt_id=?, session_nonce=?
                WHERE message_id=? AND state='prepared' AND issued_ms<=? AND expires_ms>?''',
                (attempt_id, session_nonce, message_id, now_ms, now_ms)).rowcount
            return changed == 1

    def transport_ack(self, message_id, attempt_id, response_sha256):
        if not identifier(response_sha256, 64):
            raise ValueError('explicit diagnostic response SHA required')
        with self.connection() as db:
            count = db.execute('''UPDATE diagnostic_messages
                SET state='transport_received', transport_ack_sha256=?
                WHERE message_id=? AND attempt_id=? AND state='dispatching' ''',
                (response_sha256, message_id, attempt_id)).rowcount
            if count != 1:
                raise ValueError('attempt/state mismatch')

    def complete_diagnostic_echo(self, message_id, attempt_id, verified_response_sha256):
        if not identifier(verified_response_sha256, 64):
            raise ValueError('explicit verified diagnostic receipt required')
        with self.connection() as db:
            count = db.execute('''UPDATE diagnostic_messages SET state='diagnostic_echoed'
                WHERE message_id=? AND attempt_id=? AND state='transport_received'
                AND transport_ack_sha256=?''',
                (message_id, attempt_id, verified_response_sha256)).rowcount
            if count != 1:
                raise ValueError('diagnostic receipt mismatch')

    def recover_unfinished(self):
        with self.connection() as db:
            return db.execute("""UPDATE diagnostic_messages SET state='unknown'
                WHERE state IN ('dispatching','transport_received')""").rowcount

    def read(self, message_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM diagnostic_messages WHERE message_id=?',
                             (message_id,)).fetchone()
            return dict(row) if row is not None else None
