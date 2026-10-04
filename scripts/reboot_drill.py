"""月度重启演练：把 Windows 执行节点真的重启一次，确认它能自己回来。

**这个脚本会重启生产节点。** 闸门因此卡得很死：只在当月最后一个休市日跑，
今天是交易日就直接退出。重启会让整条交易链断 5 分钟左右。

为什么要定期做：Windows 节点的无人值守恢复依赖四层设置（自动登录、不自动
更新、QMT 自动登录、开机走 XtItClient），**任何一层被人改掉都不会有提示**，
直到某次真的意外重启才发现。2026-10-04 查清这条链花了一整夜。每月跑一次，
是让它坏在我们看着的时候。

判定在 `qsys.ops.reboot_drill`（纯函数），这里只负责采集、重启与投递。

    uv run python scripts/reboot_drill.py --dry-run     # 只看今天跑不跑、采基线
    uv run python scripts/reboot_drill.py --force       # 人工指定，跳过日期判定
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import structlog

from qsys.core.calendar import last_closed_day_of_month
from qsys.ops.reboot_drill import RECOVERY_BUDGET_SECONDS, assess, should_run
from qsys.ops.services import ServiceRun
from qsys.paper.calendar import load_trading_calendar

log = structlog.get_logger("reboot-drill")

SSH_TIMEOUT = 30
# 开机后再等这么久才判定：看门狗每 5 分钟拉一次代理，太早问必然是"没起来"
SETTLE_SECONDS = 420


def _ssh(host: str, key: Path, command: str, *, timeout: int = SSH_TIMEOUT):
    return subprocess.run(
        ["ssh", "-i", str(key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         "-o", "StrictHostKeyChecking=accept-new", host, command],
        capture_output=True, timeout=timeout,
    )


def _powershell(host: str, key: Path, script: str) -> str:
    """UTF-16LE base64 送过去，绕开引号与编码。失败返回空串。"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode()
    try:
        out = _ssh(host, key, f"powershell -NoProfile -EncodedCommand {encoded}")
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("reboot_drill.powershell_failed", error=str(exc))
        return ""
    return out.stdout.decode("utf-8", errors="replace") if out.returncode == 0 else ""


_PROBE = r"""
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Write-Output ("boot_seconds_ago=" + [math]::Round(((Get-Date) - $boot).TotalSeconds))
$s = (qwinsta 2>$null | Select-String "Active" | Select-Object -First 1)
$name = if ($s) { ($s.ToString().Trim() -split '\s+')[0] } else { "" }
Write-Output ("session_name=" + ($name -replace '^>',''))
$rdp = 0
Get-WinEvent -FilterHashtable @{LogName="Security"; Id=4624; StartTime=$boot} -ErrorAction SilentlyContinue |
  ForEach-Object { $x=[xml]$_.ToXml()
    if ((($x.Event.EventData.Data|Where-Object{$_.Name -eq "LogonType"}).'#text') -eq "10") { $rdp++ } }
Write-Output ("rdp_logons=" + $rdp)
$q = Get-Process XtMiniQmt -ErrorAction SilentlyContinue
$ep = ""
if ($q) {
  $c = Get-NetTCPConnection -OwningProcess $q.Id -ErrorAction SilentlyContinue |
       Where-Object { $_.State -eq "Established" -and $_.RemoteAddress -notin @("0.0.0.0","::","127.0.0.1") } |
       Select-Object -First 1
  if ($c) { $ep = "$($c.RemoteAddress):$($c.RemotePort)" }
}
Write-Output ("broker_endpoint=" + $ep)
"""


def probe_windows(host: str, key: Path) -> dict:
    reading: dict = {}
    for line in _powershell(host, key, _PROBE).splitlines():
        key_name, _, value = line.strip().partition("=")
        if not _:
            continue
        if key_name in ("boot_seconds_ago", "rdp_logons"):
            try:
                reading[key_name] = float(value)
            except ValueError:
                pass
        else:
            reading[key_name] = value or None
    if "rdp_logons" in reading:
        reading["rdp_logons"] = int(reading["rdp_logons"])
    return reading


def probe_heartbeats(config_path: str) -> dict:
    from qsys.live.config import load_deploy_config
    from qsys.live.heartbeat import HeartbeatMonitor
    from qsys.live.redis_transport import RedisTransport

    out: dict = {}
    try:
        config = load_deploy_config(config_path)
        monitor = HeartbeatMonitor(RedisTransport.from_config(config),
                                   prefix=config.streams.heartbeat)
        for source in ("agent", "market"):
            beat = monitor.read(source)
            out[f"{source}_age"] = (
                (datetime.now() - beat.at).total_seconds()
                if beat is not None and beat.at is not None else None
            )
    except Exception as exc:          # 采不到就记 None，判定自然会判失败
        log.warning("reboot_drill.heartbeat_failed", error=str(exc))
    return out


def _alerts(config_path: str):
    from qsys.live.config import load_deploy_config
    from qsys.ops.alerts import AlertBus, LogAlertSink, WebhookAlertSink, ledger

    sinks = [LogAlertSink()]
    try:
        url = load_deploy_config(config_path).alerts.ops_url
        if url:
            sinks.append(WebhookAlertSink(url))
    except Exception as exc:
        log.warning("reboot_drill.alerts_unavailable", error=str(exc))
    return AlertBus(sinks, ledger())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"))
    parser.add_argument("--host", default="Administrator@118.25.42.149")
    parser.add_argument("--key", type=Path,
                        default=Path("/home/ubuntu/.ssh/id_ed25519_winnode"))
    parser.add_argument("--force", action="store_true",
                        help="跳过日期判定；交易日仍然拒绝")
    parser.add_argument("--dry-run", action="store_true", help="只采基线，不重启")
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    args = parser.parse_args()

    today = args.day or date.today()
    calendar = load_trading_calendar(args.config)
    drill_day = last_closed_day_of_month(calendar, today.year, today.month)

    # 这一条压过 --force：交易日绝不重启生产节点
    if calendar.is_trading_day(today):
        print(f"{today} 是交易日，拒绝重启")
        return 0

    run_it, why = should_run(today, drill_day, force=args.force)
    if not run_it:
        print(why)
        return 0

    with ServiceRun.open(args.service_runs, "reboot-drill") as run:
        run.session = today
        return _run(args, today, why, run)


def _run(args, today, why, run) -> int:
    with run.step("确认演练日", interface="交易日历") as step:
        step.detail = why

    with run.step("重启前基线", interface="SSH Windows / Redis 心跳") as step:
        before = {**probe_windows(args.host, args.key), **probe_heartbeats(args.config)}
        step.detail = (f"开机于 {before.get('boot_seconds_ago', 0) / 3600:.1f} 小时前，"
                       f"券商连接 {before.get('broker_endpoint') or '无'}")

    if args.dry_run:
        with run.step("演练", interface="SSH Windows") as step:
            step.skip("--dry-run，不重启")
        print("dry-run：今天会跑，但没有重启")
        return 0

    started = time.monotonic()
    with run.step("重启 Windows 节点", interface="SSH Windows") as step:
        _ssh(args.host, args.key, 'shutdown /r /t 5 /c "qsys monthly reboot drill"')
        step.detail = "已发出重启指令"

    with run.step("等待节点回来", interface="SSH Windows") as step:
        deadline = started + RECOVERY_BUDGET_SECONDS
        back = False
        time.sleep(30)                                  # 先让它真的断开
        while time.monotonic() < deadline:
            try:
                if _ssh(args.host, args.key, "echo up", timeout=15).returncode == 0:
                    back = True
                    break
            except (OSError, subprocess.SubprocessError):
                pass
            time.sleep(10)
        step.detail = (f"SSH 在 {time.monotonic() - started:.0f} 秒后恢复" if back
                       else f"{RECOVERY_BUDGET_SECONDS} 秒内没回来")
        if not back:
            step.fail(step.detail)

    with run.step("等链路自愈", interface="看门狗周期") as step:
        time.sleep(SETTLE_SECONDS)
        step.detail = f"等待 {SETTLE_SECONDS} 秒（看门狗每 5 分钟拉一次代理）"

    with run.step("核对恢复结果", interface="SSH Windows / Redis 心跳") as step:
        reading = {**probe_windows(args.host, args.key), **probe_heartbeats(args.config),
                   "elapsed_seconds": time.monotonic() - started}
        result = assess(reading)
        step.detail = result.summary()
        if not result.passed:
            step.fail(result.summary())

    detail = "\n".join(f"{'✅' if f.ok else '❌'} {f.name}：{f.detail}"
                       for f in result.findings)
    bus = _alerts(args.config)
    if result.passed:
        bus.info(f"月度重启演练通过（{today}）", detail)
    else:
        bus.critical(f"月度重启演练未通过（{today}）", detail)
    print(f"{today} 重启演练：{result.summary()}")
    print(detail)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
