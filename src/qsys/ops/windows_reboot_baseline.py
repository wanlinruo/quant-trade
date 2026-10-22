"""Windows forced-command 入口：只读真实基线，不提供执行任意命令的接口。"""

from __future__ import annotations

import base64
import json
import math
import os
import subprocess
import sys

COMMAND = "qsys-reboot-baseline-v1"
FIELDS = {"boot_seconds_ago", "session_name", "rdp_logons", "broker_endpoint"}
PROBE = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
if (-not $boot) { throw 'boot time unavailable' }
$sessions = @(qwinsta 2>$null)
if ($LASTEXITCODE -ne 0) { throw 'session query failed' }
$s = $sessions | Select-String "Active" | Select-Object -First 1
$name = if ($s) { (($s.ToString().Trim() -split '\s+')[0] -replace '^>','') } else { $null }
$rdp = 0
try {
  Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4624; StartTime=$boot} -ErrorAction Stop |
    ForEach-Object {
      $x = [xml]$_.ToXml()
      if ((($x.Event.EventData.Data | Where-Object {$_.Name -eq 'LogonType'}).'#text') -eq '10') { $rdp++ }
    }
} catch {
  if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { throw }
}
$q = @(Get-Process -Name XtMiniQmt -ErrorAction SilentlyContinue)
if ($q.Count -gt 1) { throw 'multiple QMT processes' }
$ep = $null
if ($q.Count -eq 1) {
  $c = Get-NetTCPConnection | Where-Object {
    $_.OwningProcess -eq $q[0].Id -and $_.State -eq 'Established' -and
    $_.RemoteAddress -notin @('0.0.0.0','::','127.0.0.1')
  } | Select-Object -First 1
  if ($c) { $ep = "$($c.RemoteAddress):$($c.RemotePort)" }
}
@{protocol=1; reading=@{
  boot_seconds_ago=[math]::Round(((Get-Date)-$boot).TotalSeconds)
  session_name=$name; rdp_logons=$rdp; broker_endpoint=$ep
}} | ConvertTo-Json -Compress
'''


def decode_reading(raw: str | bytes) -> dict:
    """读取失败必须显式失败，不把未知伪装为开机/RDP次数0。"""
    if len(raw) > 4096:
        raise ValueError("Windows基线超出大小限制")
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"protocol", "reading"}:
        raise ValueError("Windows基线协议字段错误")
    if type(data["protocol"]) is not int or data["protocol"] != 1:
        raise ValueError("Windows基线协议版本错误")
    reading = data["reading"]
    if not isinstance(reading, dict) or set(reading) != FIELDS:
        raise ValueError("Windows基线字段不完整")
    age = reading["boot_seconds_ago"]
    if type(age) not in (int, float) or not math.isfinite(age) or age < 0:
        raise ValueError("Windows开机时间无效")
    count = reading["rdp_logons"]
    if type(count) is not int or count < 0:
        raise ValueError("Windows RDP计数无效")
    for key in ("session_name", "broker_endpoint"):
        value = reading[key]
        if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
            raise ValueError("Windows基线文本无效")
    return reading


def main() -> int:
    if len(sys.argv) != 1 or os.environ.get("SSH_ORIGINAL_COMMAND") != COMMAND:
        print("baseline command rejected", file=sys.stderr)
        return 2
    if os.name != "nt":
        print("Windows required", file=sys.stderr)
        return 2
    try:
        encoded = base64.b64encode(PROBE.encode("utf-16le")).decode("ascii")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, timeout=28, check=True,
        )
        reading = decode_reading(result.stdout.decode("utf-8-sig"))
    except (OSError, subprocess.SubprocessError, ValueError):
        print("Windows baseline read failed", file=sys.stderr)
        return 1
    print(json.dumps({"protocol": 1, "reading": reading}, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
