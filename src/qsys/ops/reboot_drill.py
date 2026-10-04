"""重启演练的判定部分。

**纯函数**：只吃已经采好的读数，不重启任何东西、不碰网络。重启与采集在
``scripts/reboot_drill.py``，这样每一条判据都测得动。

为什么要定期演练：Windows 节点能不能无人值守地自己回来，取决于四层设置
（自动登录、不自动更新、QMT 自动登录、开机走 XtItClient），**任何一层被人
改掉都不会有任何提示**，直到某次真的意外重启才发现。2026-10-04 查清这条链
时就花了一整夜。每月跑一次，是让它坏在我们看着的时候。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# 开机到影子盘代理心跳恢复，2026-10-04 实测约 5.5 分钟。给到 12 分钟：
# 看门狗每 5 分钟才拉一次代理，最坏情况要等满一轮。
RECOVERY_BUDGET_SECONDS = 720
# 心跳多新才算"活着"
FRESH_SECONDS = 120


@dataclass(frozen=True, slots=True)
class Finding:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class DrillResult:
    findings: list[Finding] = field(default_factory=list)
    elapsed_seconds: float | None = None

    @property
    def passed(self) -> bool:
        return bool(self.findings) and all(f.ok for f in self.findings)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]

    def summary(self) -> str:
        if not self.findings:
            return "没有任何判据，演练未执行"
        took = (f"，耗时 {self.elapsed_seconds / 60:.1f} 分钟"
                if self.elapsed_seconds is not None else "")
        if self.passed:
            return f"{len(self.findings)} 项全部通过{took}"
        return f"{len(self.failures)}/{len(self.findings)} 项未通过{took}"


def should_run(today: date, drill_day: date, *, force: bool = False) -> tuple[bool, str]:
    """今天该不该重启生产节点。

    默认只在当月最后一个休市日跑。这个判断必须保守到偏执：误判一次，就是在
    交易时段把实盘链路打断 5 分钟。
    """
    if force:
        return True, "人工指定，跳过日期判定"
    if today != drill_day:
        return False, f"今天 {today} 不是本月演练日（{drill_day}）"
    return True, f"{today} 是本月最后一个休市日"


def assess(reading: dict) -> DrillResult:
    """从重启后的一次采集里判断整条链有没有自己回来。

    每一条都对应 2026-10-04 真实踩过的坑，顺序就是故障发生的顺序。
    """
    findings: list[Finding] = []

    booted = reading.get("boot_seconds_ago")
    findings.append(Finding(
        "机器已重启", booted is not None and booted < RECOVERY_BUDGET_SECONDS,
        f"开机于 {booted:.0f} 秒前" if booted is not None else "取不到开机时刻",
    ))

    # 自动登录：必须是控制台会话，且没有人 RDP 进来救场
    session = str(reading.get("session_name") or "")
    findings.append(Finding(
        "Windows 自动登录", session.lower().startswith("console"),
        f"会话 {session or '无'}" + ("" if session else "（没有任何登录会话）"),
    ))
    rdp = reading.get("rdp_logons")
    findings.append(Finding(
        "全程无人介入", rdp == 0,
        "开机后无 RDP 登录" if rdp == 0 else f"开机后有 {rdp} 次 RDP 登录，本次不算无人值守",
    ))

    findings.append(Finding(
        "QMT 已连券商", bool(reading.get("broker_endpoint")),
        str(reading.get("broker_endpoint") or "QMT 没有任何券商连接"),
    ))

    for name, source in (("影子盘代理心跳", "agent"), ("行情网关心跳", "market")):
        age = reading.get(f"{source}_age")
        findings.append(Finding(
            name, age is not None and age < FRESH_SECONDS,
            f"{age:.0f} 秒前" if age is not None else "无心跳",
        ))

    return DrillResult(findings, reading.get("elapsed_seconds"))
