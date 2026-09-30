"""基础设施实时看护：磁盘、证书、失败单元、急停、Redis。

这些以前**只有每晚 20:00 那一次机会**，或者根本没人看。证书续期失效躺了一整天
（2026-09-26）、fwupd 单元长期 failed 污染 `systemctl --failed`（2026-09-30），
两次都是偶然翻出来的，不是系统告诉我的。

两条自律：

**边沿触发**。出事报一次、恢复报一次。国庆七天 Windows 节点不开机，应该是
一条告警，不是两千条——连续报警和不报警一样没用，因为人会开始无视它。

**纯函数判定**。`survey()` 只吃已经采好的读数，不碰网络、不碰磁盘，所以每条
阈值与措辞都测得动；采集在调用方。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from qsys.ops.alerts import Severity
from qsys.ops.storage import FREE_FAIL_BYTES, FREE_WARN_BYTES, GIB

CERT_WARN_DAYS, CERT_FAIL_DAYS = 21, 10


@dataclass(frozen=True, slots=True)
class Finding:
    key: str            # 去重键：同一件事每次要给同一个 key
    severity: Severity
    title: str
    detail: str = ""


def survey(reading: dict) -> list[Finding]:
    """从一次采集读数里挑出「现在有问题的事」。

    读数里取不到的项一律跳过，不臆断——「问不出来」在这里保持沉默，由每晚质检
    和日报去说。实时通道只报确凿的坏消息，否则很快就没人看了。
    """
    out: list[Finding] = []

    free = reading.get("disk_free_bytes")
    if free is not None:
        if free < FREE_FAIL_BYTES:
            out.append(Finding("disk", Severity.CRITICAL, "磁盘即将写满",
                               f"剩余 {free / GIB:.1f} GiB，低于 {FREE_FAIL_BYTES // GIB} GiB"))
        elif free < FREE_WARN_BYTES:
            out.append(Finding("disk", Severity.WARNING, "磁盘余量偏低",
                               f"剩余 {free / GIB:.1f} GiB，低于 {FREE_WARN_BYTES // GIB} GiB"))

    days = reading.get("cert_days")
    if days is not None:
        if days < CERT_FAIL_DAYS:
            out.append(Finding("cert", Severity.CRITICAL, "站点证书即将过期",
                               f"剩余 {days} 天。先看 systemctl status certbot.service"))
        elif days < CERT_WARN_DAYS:
            out.append(Finding("cert", Severity.WARNING, "站点证书进入续期窗口",
                               f"剩余 {days} 天"))

    # 一个单元一条：两个单元先后坏掉时，不能因为「已经报过了」而漏掉第二个
    for unit in reading.get("failed_units") or ():
        out.append(Finding(f"unit:{unit}", Severity.CRITICAL, "系统单元失败", unit))

    kill = reading.get("kill_switch")
    if kill:
        out.append(Finding("kill", Severity.CRITICAL, "急停开关已触发", str(kill)))

    if reading.get("redis_error"):
        out.append(Finding("redis", Severity.CRITICAL, "Redis 不可达",
                           str(reading["redis_error"])))

    return out


@dataclass(frozen=True, slots=True)
class Notice:
    severity: Severity
    title: str
    detail: str


class EdgeTracker:
    """只在状态变化时出声。

    `update` 吃「此刻仍然存在的问题」，吐「这一轮需要发出去的消息」：
    新出现的报一次，消失的报一次恢复，一直在的闭嘴。
    """

    def __init__(self) -> None:
        self._open: dict[str, Finding] = {}

    def update(self, findings: Iterable[Finding]) -> list[Notice]:
        current = {f.key: f for f in findings}
        notices = [
            Notice(f.severity, f.title, f.detail)
            for key, f in current.items()
            if key not in self._open
        ]
        notices += [
            Notice(Severity.INFO, f"{f.title}：已恢复", f.detail)
            for key, f in self._open.items()
            if key not in current
        ]
        self._open = current
        return notices

    @property
    def open_keys(self) -> set[str]:
        return set(self._open)
