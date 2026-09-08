"""告警链路。

告警是旁路，不是主路：任何一个 sink 发送失败都不得中断主流程，
更不得让其它 sink 收不到——实盘出问题时最不该发生的就是
「告警系统自己挂了导致你不知道出了问题」。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import structlog

log = structlog.get_logger(__name__)


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class Alert:
    severity: Severity
    title: str
    detail: str
    at: datetime

    def as_text(self) -> str:
        stamp = self.at.strftime("%Y-%m-%d %H:%M:%S")
        head = f"[{self.severity.value}] {stamp} {self.title}"
        return f"{head}\n{self.detail}" if self.detail else head


class AlertSink(ABC):
    @abstractmethod
    def send(self, alert: Alert) -> None: ...


class LogAlertSink(AlertSink):
    """始终装配的兜底 sink：至少日志里留得下。"""

    def send(self, alert: Alert) -> None:
        emit = {
            Severity.INFO: log.info,
            Severity.WARNING: log.warning,
            Severity.CRITICAL: log.error,
        }[alert.severity]
        emit("alert", title=alert.title, detail=alert.detail, at=alert.at.isoformat())


class RecordingAlertSink(AlertSink):
    """测试用。"""

    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


def _default_poster(url: str, payload: dict, timeout: float) -> None:
    import json
    import urllib.request

    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request, timeout=timeout).close()


class WebhookAlertSink(AlertSink):
    """通用 webhook —— 企业微信 / 钉钉 / Server 酱等均可对接。

    poster 可注入以便测试；默认用标准库，不引入新依赖。
    """

    def __init__(
        self, url: str, *, timeout: float = 5.0,
        poster: Callable[[str, dict, float], None] | None = None,
    ) -> None:
        self._url = url
        self._timeout = timeout
        self._poster = poster or _default_poster

    def send(self, alert: Alert) -> None:
        self._poster(
            self._url,
            {"msgtype": "text", "text": {"content": alert.as_text()}},
            self._timeout,
        )


class AlertBus:
    def __init__(self, sinks: Sequence[AlertSink]) -> None:
        self._sinks = tuple(sinks)

    def publish(self, alert: Alert) -> None:
        for sink in self._sinks:
            try:
                sink.send(alert)
            except Exception as exc:  # 一个 sink 挂了不能影响其它 sink 与主流程
                log.error("alert.sink_failed", sink=type(sink).__name__, error=str(exc))

    def info(self, title: str, detail: str = "") -> None:
        self.publish(Alert(Severity.INFO, title, detail, datetime.now()))

    def warning(self, title: str, detail: str = "") -> None:
        self.publish(Alert(Severity.WARNING, title, detail, datetime.now()))

    def critical(self, title: str, detail: str = "") -> None:
        self.publish(Alert(Severity.CRITICAL, title, detail, datetime.now()))
