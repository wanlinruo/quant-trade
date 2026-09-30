"""节点基础设施的只读观测：systemd 与内存。

磁盘不在这里——`qsys.ops.storage.disk_status` 早就有了，连阈值一起，
再写一份只会让两处阈值慢慢漂开。

日报与实时看护共用。**全部只读**，一律不改任何状态。

拿不到就返回 None，不返回"看起来没事"——在 Mac 上跑测试、在没有 systemd 的
容器里跑、权限不够，都属于"问不出来"。把问不出来当成健康，是监控里最贵的错：
2026-09-26 证书续期失效躺了一整天，就是因为没人问、也没人发现没人问。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

TIMEOUT = 5.0

_SHOW_PROPS = ("ActiveState", "SubState", "Result", "ExecMainStatus",
               "ExecMainExitTimestamp", "ActiveEnterTimestamp",
               "NextElapseUSecRealtime", "LastTriggerUSec")

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT)


def _text(command: list[str], runner: Runner | None) -> str | None:
    try:
        out = (runner or _run)(command)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        log.info("infra.command_unavailable", command=command[0], error=str(exc))
        return None
    return out.stdout if out.returncode == 0 else None


def systemctl_show(unit: str, *, props: tuple[str, ...] = _SHOW_PROPS,
                   runner: Runner | None = None) -> dict[str, str]:
    """只读查询 systemd 单元属性；拿不到（非 Linux、无权限）返回空。"""
    out = _text(["systemctl", "show", unit, *(f"-p{p}" for p in props)], runner)
    if out is None:
        return {}
    props_read = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {k: v for k, v in props_read.items() if v and v != "n/a"}


def failed_units(runner: Runner | None = None) -> tuple[str, ...] | None:
    """当前处于 failed 的单元名；问不出来返回 None（区别于"一个都没有"）。"""
    out = _text(["systemctl", "--failed", "--no-legend", "--plain"], runner)
    if out is None:
        return None
    return tuple(line.split()[0] for line in out.splitlines() if line.split())


@dataclass(frozen=True, slots=True)
class Memory:
    total_gib: float
    available_gib: float

    @property
    def used_pct(self) -> int:
        return round(100 * (1 - self.available_gib / self.total_gib)) if self.total_gib else 0


def memory(meminfo: Path | str = "/proc/meminfo") -> Memory | None:
    """只读 /proc/meminfo；非 Linux 上没有这个文件，返回 None。

    用 MemAvailable 而不是 MemFree：页缓存随时可回收，MemFree 在健康的机器上
    也常年很低，拿它报警等于天天狼来了。
    """
    try:
        raw = Path(meminfo).read_text(encoding="utf-8")
    except OSError:
        return None
    fields = {}
    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            fields[key] = int(parts[0])          # 单位 kB
    total, available = fields.get("MemTotal"), fields.get("MemAvailable")
    if not total or available is None:
        return None
    mib = 1024 * 1024
    return Memory(total / mib, available / mib)
