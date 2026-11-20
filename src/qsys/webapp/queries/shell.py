"""顶栏状态查询（设计文档 §5、§10）。

多个标签页同时轮询时，TTL 内共用一次底层读取；缓存时长不超过停机闸读取结果
自身的有效期，过期后重读，不把已失效的“已解除”继续返回。读取异常一律报“未知”。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from qsys.webapp.live_kill_status import LiveKillStatus
from qsys.webapp.contracts.shell import KillSource, KillStatus, LivePosture, ShellStatus

MISSING = {
    "qmt": "QMT 客户端与执行器状态待计划 03/07 的结构化采集接入",
}
ALERTS_UNCONFIGURED = "告警存储未配置"
ALERTS_UNREADABLE = "告警存储不可读"
_LIVE = {
    "stopped": ("reduce_only", "仅允许减仓"),
    "clear": ("trading", "实盘运行"),
    "unknown": ("unknown", "实盘状态未知"),
}
_UNKNOWN = KillStatus(state="unknown", label="停机状态未知",
                      message="无法确认当前停机状态，请核对异常来源。", sources=[])


class KillReader(Protocol):
    def read(self) -> LiveKillStatus: ...


def build_status(status: LiveKillStatus | None, checked_at: datetime, *,
                 alerts_open: int | None = None, alerts_reason: str | None = ALERTS_UNCONFIGURED) -> ShellStatus:
    if status is None or status.state not in _LIVE:
        kill, state, agent_at = _UNKNOWN, "unknown", None
    else:
        state = status.state
        kill = KillStatus(state=state, label=status.label, message=status.message, sources=[
            KillSource(id=s.id, name=s.name, state=s.state, reason=s.reason,
                       recorded_at=datetime.fromisoformat(s.recorded_at) if s.recorded_at else None)
            for s in status.sources])
        agent = next((s for s in status.sources if s.id == "agent"), None)
        agent_at = datetime.fromisoformat(agent.recorded_at) if agent and agent.recorded_at else None
    live_state, live_label = _LIVE[state]
    return ShellStatus(checked_at=checked_at, kill=kill,
                       live=LivePosture(state=live_state, label=live_label),
                       agent_heartbeat_at=agent_at, alerts_open=alerts_open,
                       missing=MISSING | ({"alerts_open": alerts_reason} if alerts_reason else {}))


class ShellQuery:
    def __init__(self, reader: KillReader, ttl_seconds: float = 5.0,
                 clock: Callable[[], float] = time.monotonic, *,
                 alerts: Callable[[], int | None] | None = None) -> None:
        self._reader, self._ttl, self._clock = reader, ttl_seconds, clock
        self._alerts = alerts
        self._lock = threading.Lock()
        self._value: ShellStatus | None = None
        self._expires = 0.0

    def get(self) -> ShellStatus:
        with self._lock:
            now = self._clock()
            if self._value is not None and now < self._expires:
                return self._value
            checked_at = datetime.now().replace(microsecond=0)
            try:
                status: LiveKillStatus | None = self._reader.read()
            except Exception:
                status = None
            ttl = self._ttl if status is None else min(self._ttl, max(0.0, status.valid_for_seconds))
            if self._alerts is None:
                open_count, reason = None, ALERTS_UNCONFIGURED
            else:
                open_count = self._alerts()
                reason = ALERTS_UNREADABLE if open_count is None else None
            self._value = build_status(status, checked_at, alerts_open=open_count, alerts_reason=reason)
            self._expires = now + ttl
            return self._value
