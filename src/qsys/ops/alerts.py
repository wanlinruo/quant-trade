"""告警链路。

告警是旁路，不是主路：任何一个 sink 发送失败都不得中断主流程，
更不得让其它 sink 收不到——实盘出问题时最不该发生的就是
「告警系统自己挂了导致你不知道出了问题」。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

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


def send_email(
    config, subject: str, text: str, html: str = "", *, sender=None,
) -> None:
    """用标准库发一封信，不引入新依赖。

    ``sender`` 可注入以便测试——真去连 SMTP 的测试既慢又会把密码带进 CI。
    """
    from email.message import EmailMessage

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.from_address
    message["To"] = ", ".join(config.recipients)
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    if sender is not None:
        sender(message)
        return

    import smtplib

    if config.use_ssl:
        client = smtplib.SMTP_SSL(
            config.host, config.port, timeout=config.timeout_seconds
        )
    else:
        client = smtplib.SMTP(config.host, config.port, timeout=config.timeout_seconds)
    with client:
        if not config.use_ssl:
            client.starttls()
        client.login(config.username, config.password)
        client.send_message(message)


class AlertLedger:
    """按天把每条告警的投递结果追加成 JSONL，供日报自检。

    通知链路自己挂了是最难发现的一类故障：机器人 key 被撤、群被解散、
    出口网络被墙，全都**静默**——告警发不出去，而「告警发不出去」这件事
    本身也要靠告警才能知道。所以留一份账，日报每天报一次
    「今天推了几条、失败几条」，把静默变成可核对的数字。

    写失败一律吞掉：留账是旁路的旁路，绝不能反过来影响主流程。
    """

    def __init__(self, root) -> None:
        self._root = Path(root)

    def record(self, alert: Alert, delivered: int, failed: Sequence[str]) -> None:
        line = {
            "at": alert.at.isoformat(timespec="seconds"),
            "severity": alert.severity.value,
            "title": alert.title,
            "delivered": delivered,
            "failed": list(failed),
        }
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            path = self._root / f"{alert.at:%Y-%m-%d}.jsonl"
            with path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("alert.ledger_failed", error=str(exc))

    def read(self, day) -> list[dict]:
        path = self._root / f"{day:%Y-%m-%d}.jsonl"
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return []
        out = []
        for line in raw.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:            # 半行（进程被 kill）不该毁掉整份账
                log.warning("alert.ledger_unreadable", path=str(path))
        return out


# 各进程各写各的，按天一个文件，日报统一读。相对路径，进程的工作目录都是仓库根。
LEDGER_DIR = Path("var/alerts")


def ledger(root=None) -> AlertLedger:
    return AlertLedger(Path(root) if root else LEDGER_DIR)


class AlertBus:
    def __init__(self, sinks: Sequence[AlertSink],
                 ledger: AlertLedger | None = None) -> None:
        self._sinks = tuple(sinks)
        self._ledger = ledger

    def publish(self, alert: Alert) -> None:
        delivered, failed = 0, []
        for sink in self._sinks:
            try:
                sink.send(alert)
                delivered += 1
            except Exception as exc:  # 一个 sink 挂了不能影响其它 sink 与主流程
                failed.append(type(sink).__name__)
                log.error("alert.sink_failed", sink=type(sink).__name__, error=str(exc))
        if self._ledger is not None:
            self._ledger.record(alert, delivered, failed)

    def info(self, title: str, detail: str = "") -> None:
        self.publish(Alert(Severity.INFO, title, detail, datetime.now()))

    def warning(self, title: str, detail: str = "") -> None:
        self.publish(Alert(Severity.WARNING, title, detail, datetime.now()))

    def critical(self, title: str, detail: str = "") -> None:
        self.publish(Alert(Severity.CRITICAL, title, detail, datetime.now()))
