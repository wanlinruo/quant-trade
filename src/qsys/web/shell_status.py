"""新前端顶栏状态的只读汇总（#662，设计文档 §5、§10）。

多个浏览器标签同时轮询时，TTL 内共用一次底层读取，避免每次请求都读
共享隧道上的 Redis。缓存时长不超过停机闸读取结果自身的有效期，过期后
重新读取，不把已失效的“已解除”继续返回。读取异常一律报“未知”。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Callable, Protocol

from qsys.web.live_kill_status import LiveKillStatus

SCHEMA_VERSION = 1
QMT_REASON = "第 03 份计划提供结构化链路记录后接入"
ALERTS_REASON = "第 04 份计划提供告警汇总后接入"

_LIVE = {
    "stopped": ("reduce_only", "仅允许减仓"),
    "clear": ("trading", "实盘运行"),
    "unknown": ("unknown", "实盘状态未知"),
}
_UNKNOWN_KILL = {
    "state": "unknown",
    "label": "停机状态未知",
    "message": "无法确认当前停机状态，请核对异常来源。",
    "sources": [],
}


class KillReader(Protocol):
    def read(self) -> LiveKillStatus: ...


def _build(status: LiveKillStatus | None, checked_at: str) -> dict[str, Any]:
    if status is None or status.state not in _LIVE:
        kill = dict(_UNKNOWN_KILL)
        agent_at = None
        state = "unknown"
    else:
        state = status.state
        kill = {
            "state": state, "label": status.label, "message": status.message,
            "sources": [
                {"id": s.id, "name": s.name, "state": s.state, "reason": s.reason,
                 "recorded_at": s.recorded_at}
                for s in status.sources
            ],
        }
        agent_at = next((s.recorded_at for s in status.sources if s.id == "agent"), None)
    live_state, live_label = _LIVE[state]
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at": checked_at,
        "kill": kill,
        "live": {"state": live_state, "label": live_label},
        "agent_heartbeat_at": agent_at,
        "qmt": None, "qmt_reason": QMT_REASON,
        "alerts_open": None, "alerts_reason": ALERTS_REASON,
    }


class ShellStatusCache:
    def __init__(self, reader: KillReader, ttl_seconds: float = 5.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._reader = reader
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._value: dict[str, Any] | None = None
        self._expires = 0.0

    def get(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            if self._value is not None and now < self._expires:
                return self._value
            checked_at = datetime.now().isoformat(timespec="seconds")
            try:
                status: LiveKillStatus | None = self._reader.read()
            except Exception:
                status = None
            ttl = self._ttl
            if status is not None:
                ttl = min(ttl, max(0.0, status.valid_for_seconds))
            self._value = _build(status, checked_at)
            self._expires = now + ttl
            return self._value
