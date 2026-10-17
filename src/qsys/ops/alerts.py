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

    def accepts(self, alert: Alert) -> bool:
        """这条告警会不会真的从这个 sink 出去。

        过滤掉的不算投递——留账里的「今天推了几条」要是把挡下的也算进去，
        那个数字就不能用来判断通知链路好不好了（2026-10-03 自检时发现）。
        """
        return True


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


_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}


class MinSeveritySink(AlertSink):
    """只转发够严重的告警。

    实盘对账失败、心跳超时、拒单熔断**既是交易事件也是运维事故**：交易者要知道
    钱出了问题，管理者要知道系统停了。这类 CRITICAL 两边都该收到。
    其余的不转——每天的竞价决策推进运维群，只会把真正的事故淹掉。
    """

    def __init__(self, inner: AlertSink,
                 minimum: Severity = Severity.CRITICAL) -> None:
        self._inner, self._minimum = inner, minimum

    def accepts(self, alert: Alert) -> bool:
        return _RANK[alert.severity] >= _RANK[self._minimum]

    def send(self, alert: Alert) -> None:
        if self.accepts(alert):
            self._inner.send(alert)


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
        paths = [self._root / f"{day:%Y-%m-%d}.jsonl"]
        paths.extend(
            self._root / service / f"{day:%Y-%m-%d}.jsonl"
            for service in sorted(SERVICE_LEDGER_NAMESPACES)
            if (self._root / service).is_dir()
        )
        out = []
        for path in paths:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in raw.splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:        # 半行（进程被 kill）不该毁掉整份账
                    log.warning("alert.ledger_unreadable", path=str(path))
        return out


def strategy_sinks(strategy_url: str, ops_url: str = "", *,
                   timeout: float = 5.0) -> list[AlertSink]:
    """一条交易链的告警去处：策略群收全部，运维群只收 CRITICAL。

    两个地址相同时（只配了一个机器人）不重复装配，否则每条严重告警会发两遍。
    """
    sinks: list[AlertSink] = [LogAlertSink()]
    if strategy_url:
        sinks.append(WebhookAlertSink(strategy_url, timeout=timeout))
    if ops_url and ops_url != strategy_url:
        sinks.append(MinSeveritySink(WebhookAlertSink(ops_url, timeout=timeout)))
    return sinks


# 各进程各写各的，按天一个文件，日报统一读。相对路径，进程的工作目录都是仓库根。
LEDGER_DIR = Path("var/alerts")
SERVICE_LEDGER_NAMESPACES = frozenset({"health-observer", "service-checks"})


def ledger(root=None) -> AlertLedger:
    return AlertLedger(Path(root) if root else LEDGER_DIR)


def _accepts(sink, alert: Alert) -> bool:
    """`accepts` 是可选约定：没有就当全收。

    sink 只要有 `send` 就该能用——调用方自己写的 sink 不该因为少一个方法
    就把整条告警链打死。问这一句本身出错也按全收处理，宁可多发不可不发。
    """
    try:
        return bool(getattr(sink, "accepts", _ALWAYS)(alert))
    except Exception as exc:
        log.warning("alert.accepts_failed", sink=type(sink).__name__, error=str(exc))
        return True


def _ALWAYS(alert: Alert) -> bool:
    return True


class AlertBus:
    def __init__(self, sinks: Sequence[AlertSink],
                 ledger: AlertLedger | None = None) -> None:
        self._sinks = tuple(sinks)
        self._ledger = ledger

    def publish(self, alert: Alert) -> None:
        delivered, failed = 0, []
        for sink in self._sinks:
            if not _accepts(sink, alert):
                continue
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
