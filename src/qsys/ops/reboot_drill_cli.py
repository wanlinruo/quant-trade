"""Windows 重启演练控制面入口。必须显式选择 --dry-run 或 --execute。

--execute 还需本次获批的上海日期与完整目标主机确认；这些参数只是操作员
声明，不替代维护者明确审批。--force 仅绕过月度日期选择，交易日仍拒绝。
--day 仅用于 dry-run 模拟，不得替换真实执行日期。dry-run 仍采集真实基线。
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog

from qsys.core.calendar import last_closed_day_of_month
from qsys.ops.reboot_drill import RECOVERY_BUDGET_SECONDS, assess, should_run
from qsys.ops.services import ServiceRun
from qsys.paper.calendar import load_trading_calendar

log = structlog.get_logger("reboot-drill")

SSH_TIMEOUT = 30
# 开机后再等这么久才判定：看门狗每 5 分钟拉一次代理，太早问必然是"没起来"
SETTLE_SECONDS = 420


def _ssh(host: str, key: Path, command: str, *, timeout: int = SSH_TIMEOUT,
         known_hosts: Path | None = None):
    options = ["-o", "StrictHostKeyChecking=yes", "-o", "IdentitiesOnly=yes",
               "-o", "IdentityAgent=none"]
    if known_hosts is not None:
        options += ["-o", f"UserKnownHostsFile={known_hosts}"]
    return subprocess.run(
        ["ssh", "-i", str(key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         *options, host, command],
        capture_output=True, timeout=timeout,
    )


def _powershell(host: str, key: Path, script: str, *, known_hosts: Path | None = None) -> str:
    """UTF-16LE base64 送过去，绕开引号与编码。失败返回空串。"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode()
    try:
        out = _ssh(host, key, f"powershell -NoProfile -EncodedCommand {encoded}",
                   known_hosts=known_hosts)
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


def probe_windows(host: str, key: Path, *, known_hosts: Path | None = None) -> dict:
    reading: dict = {}
    for line in _powershell(host, key, _PROBE, known_hosts=known_hosts).splitlines():
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
    from qsys.live.readonly_transport import readonly_from_config

    out: dict = {}
    try:
        config = load_deploy_config(config_path)
        monitor = HeartbeatMonitor(readonly_from_config(config),
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


def probe_windows_readonly(host: str, key: Path, *, known_hosts: Path) -> dict:
    from qsys.ops.windows_reboot_baseline import COMMAND, decode_reading

    result = _ssh(host, key, COMMAND, known_hosts=known_hosts)
    if result.returncode:
        raise RuntimeError("Windows只读基线入口失败")
    return decode_reading(result.stdout)


def _alerts(config_path: str, root: Path | None = None):
    from qsys.live.config import load_deploy_config
    from qsys.ops.alerts import AlertBus, LogAlertSink, WebhookAlertSink, ledger

    sinks = [LogAlertSink()]
    try:
        url = load_deploy_config(config_path).alerts.ops_url
        if url:
            sinks.append(WebhookAlertSink(url))
    except Exception as exc:
        log.warning("reboot_drill.alerts_unavailable", error=str(exc))
    return AlertBus(sinks, ledger(root))


def _today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _execution_confirmed(args) -> bool:
    today = _today()
    return (
        args.execute and not args.dry_run
        and args.approved_day == today
        and args.approved_host == args.host
        and (args.day is None or args.day == today)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"))
    parser.add_argument("--alert-ledger", type=Path, help="专属告警流水目录")
    parser.add_argument("--known-hosts", type=Path, help="预置且只读的 SSH host key 文件")
    parser.add_argument("--win-transport", choices=("legacy", "readonly-baseline"), default="legacy")
    parser.add_argument("--host", default="Administrator@118.25.42.149")
    parser.add_argument("--key", type=Path,
                        default=Path("/home/ubuntu/.ssh/id_ed25519_winnode"))
    parser.add_argument("--force", action="store_true",
                        help="跳过日期判定；交易日仍然拒绝")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只采真实基线，不重启")
    mode.add_argument("--execute", action="store_true", help="本次演练获批后显式执行")
    parser.add_argument("--approved-day", type=date.fromisoformat,
                        help="本次获批的上海日期 YYYY-MM-DD")
    parser.add_argument("--approved-host", help="本次获批的完整 SSH 目标，与 --host 完全相同")
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    if args.win_transport == "readonly-baseline" and (not args.dry_run or args.known_hosts is None):
        parser.error("只读Windows基线入口仅允许--dry-run，并须显式--known-hosts")
    if not args.host or args.host.startswith("-"):
        parser.error("--host 必须是 SSH 目标，不能是选项")
    if args.execute and not _execution_confirmed(args):
        parser.error("执行需 --approved-day 为当前上海日期、--approved-host 与 --host 一致；"
                     "--day 不得替换真实执行日；请先取得维护者本次明确审批")
    if args.dry_run and (args.approved_day is not None or args.approved_host is not None):
        parser.error("dry-run 不接受执行确认参数")

    today = args.day or _today()
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
        probe = probe_windows_readonly if getattr(args, "win_transport", "legacy") == "readonly-baseline" else probe_windows
        before = {**probe(args.host, args.key, known_hosts=args.known_hosts), **probe_heartbeats(args.config)}
        boot_age = before.get("boot_seconds_ago")
        boot_detail = (f"开机于 {boot_age / 3600:.1f} 小时前"
                       if boot_age is not None else "取不到开机时刻")
        step.detail = f"{boot_detail}，券商连接 {before.get('broker_endpoint') or '无'}"

    if args.dry_run:
        with run.step("演练", interface="SSH Windows") as step:
            step.skip("--dry-run，不重启")
        run.skip("仅采基线，未执行重启演练")
        print("dry-run：今天会跑，但没有重启")
        return 0

    started = time.monotonic()
    with run.step("重启 Windows 节点", interface="SSH Windows") as step:
        # 基线采集期间可能跨日；危险命令前再次核对当日声明。
        if not _execution_confirmed(args) or today != _today():
            step.fail("执行确认已失效，未发出重启指令")
            return 1
        try:
            command = _ssh(args.host, args.key,
                           'shutdown /r /t 5 /c "qsys monthly reboot drill"',
                           known_hosts=args.known_hosts)
        except (OSError, subprocess.SubprocessError):
            # SSH 超时可能发生在远端已接受命令后；不得自动重试。
            step.fail("重启命令传输异常，远端是否接受未知；停止并人工核验，不自动重试")
            _alerts(args.config, args.alert_ledger).critical("重启演练命令结果未知", step.detail)
            return 1
        if command.returncode != 0:
            step.fail(f"重启命令 SSH 返回 {command.returncode}，未确认接受；停止且不自动重试")
            _alerts(args.config, args.alert_ledger).critical("重启演练命令失败", step.detail)
            return 1
        step.detail = "远端已接受重启命令，尚待核验恢复"

    with run.step("等待节点回来", interface="SSH Windows") as step:
        deadline = started + RECOVERY_BUDGET_SECONDS
        back = False
        time.sleep(30)                                  # 先让它真的断开
        while time.monotonic() < deadline:
            try:
                if _ssh(args.host, args.key, "echo up", timeout=15,
                        known_hosts=args.known_hosts).returncode == 0:
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
        reading = {**probe_windows(args.host, args.key, known_hosts=args.known_hosts), **probe_heartbeats(args.config),
                   "elapsed_seconds": time.monotonic() - started}
        result = assess(reading)
        step.detail = result.summary()
        if not result.passed:
            step.fail(result.summary())

    detail = "\n".join(f"{'✅' if f.ok else '❌'} {f.name}：{f.detail}"
                       for f in result.findings)
    bus = _alerts(args.config, args.alert_ledger)
    if result.passed:
        bus.info(f"月度重启演练通过（{today}）", detail)
    else:
        bus.critical(f"月度重启演练未通过（{today}）", detail)
    print(f"{today} 重启演练：{result.summary()}")
    print(detail)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
