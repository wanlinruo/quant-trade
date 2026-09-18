"""连续失败的降噪与告警。

2026-09-18 的教训：Windows 执行代理 05:06 崩溃后，影子核心把同一条
「券商账户快照已过期」每秒刷一遍，5.5 小时约 2 万条，却没有任何告警——
日志淹没了信息，人工发现时当天 09:30 的竞价事件已经过期没跑。

规则：同一条错误按间隔降频打印；连续失败超过阈值报一次 critical；
恢复时报一条 info。错误内容变化立即打印，不被上一条的节流挡住。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol


class Alerts(Protocol):
    def critical(self, title: str, detail: str = "") -> None: ...

    def info(self, title: str, detail: str = "") -> None: ...


@dataclass
class TickFailureGuard:
    alerts: Alerts
    log_interval: timedelta = timedelta(minutes=1)
    alert_after: timedelta = timedelta(minutes=5)
    _error: str = field(default="", init=False)
    _first_at: datetime | None = field(default=None, init=False)
    _last_log_at: datetime | None = field(default=None, init=False)
    _alerted: bool = field(default=False, init=False)

    def failed(self, error: str, *, now: datetime) -> bool:
        """记一次失败；返回这次是否应当打印日志。"""
        changed = error != self._error
        if changed or self._first_at is None:
            self._error, self._first_at, self._alerted = error, now, False
        should_log = (
            changed
            or self._last_log_at is None
            or now - self._last_log_at >= self.log_interval
        )
        if should_log:
            self._last_log_at = now
        if not self._alerted and now - self._first_at >= self.alert_after:
            self._alerted = True
            minutes = int(self.alert_after.total_seconds() // 60)
            self.alerts.critical(
                f"影子核心连续失败超过 {minutes} 分钟",
                f"自 {self._first_at:%Y-%m-%d %H:%M:%S} 起持续失败：{error}",
            )
        return should_log

    def succeeded(self, *, now: datetime) -> None:
        if self._first_at is None:
            return
        if self._alerted:
            self.alerts.info(
                "影子核心已恢复",
                f"{self._first_at:%Y-%m-%d %H:%M:%S} ~ {now:%Y-%m-%d %H:%M:%S} "
                f"的连续失败已结束：{self._error}",
            )
        self._error, self._first_at, self._last_log_at = "", None, None
        self._alerted = False
