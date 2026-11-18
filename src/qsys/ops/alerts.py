"""告警链路（#662 计划 03 起发布结构化事件）。

告警是旁路，不是主路：任何一个 sink 发送失败都不得中断主流程，
更不得让其它 sink 收不到——实盘出问题时最不该发生的就是
「告警系统自己挂了导致你不知道出了问题」。

唯一入口 ``AlertBus.emit(key, …)``：按受管键清单（``alert_keys``）构造事件 → 发送 →
连同投递结果写入事件存储（``alert_events.AlertEventLog``）。通知文本由事件生成。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog

from qsys.ops.alert_events import (
    SERVICE_LEDGER_NAMESPACES, AlertEvent, AlertEventLog, Severity, new_event_id, now_shanghai, render_text,
)
from qsys.ops.alert_keys import lookup

log = structlog.get_logger(__name__)


class AlertSink(ABC):
    @abstractmethod
    def send(self, event: AlertEvent) -> None: ...

    def accepts(self, event: AlertEvent) -> bool:
        """这条告警会不会真的从这个 sink 出去。

        过滤掉的不算投递——留账里的「今天推了几条」要是把挡下的也算进去，
        那个数字就不能用来判断通知链路好不好了（2026-10-03 自检时发现）。
        """
        return True


class LogAlertSink(AlertSink):
    """始终装配的兜底 sink：至少日志里留得下。"""

    def send(self, event: AlertEvent) -> None:
        emit = {
            Severity.INFO: log.info,
            Severity.WARNING: log.warning,
            Severity.CRITICAL: log.error,
        }[event.severity]
        emit("alert", key=event.key, subject=event.subject, state=event.state, title=event.title,
             detail=event.detail, at=event.occurred_at.isoformat())


class RecordingAlertSink(AlertSink):
    """测试用。"""

    def __init__(self) -> None:
        self.events: list[AlertEvent] = []

    def send(self, event: AlertEvent) -> None:
        self.events.append(event)


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

    def accepts(self, event: AlertEvent) -> bool:
        return _RANK[event.severity] >= _RANK[self._minimum]

    def send(self, event: AlertEvent) -> None:
        if self.accepts(event):
            self._inner.send(event)


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

    def send(self, event: AlertEvent) -> None:
        self._poster(
            self._url,
            {"msgtype": "text", "text": {"content": render_text(event)}},
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


class LegacyLedger:
    """旧格式告警账本（``<日期>.jsonl``）的只读读取器。

    计划 03 之前各组件按天写的投递流水；迁移期尚未升级的生产者（N-1）也仍写这种格式。
    日报合并读取它与事件存储；全部生产者发布满 14 天后在合入计划 02 中删除本类，历史文件保留。
    """

    def __init__(self, root) -> None:
        self._root = Path(root)

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


# 按天留存，日报合并显式组件目录与旧flat历史；legacy默认相对仓库根，独立入口须传绝对路径。
LEDGER_DIR = Path("var/alerts")


def ledger(root=None) -> AlertEventLog:
    """告警事件存储（与旧账本同一目录，文件名 <日期>.events.jsonl）。"""
    return AlertEventLog(Path(root) if root else LEDGER_DIR)


def _accepts(sink, event: AlertEvent) -> bool:
    """`accepts` 是可选约定：没有就当全收。

    sink 只要有 `send` 就该能用——调用方自己写的 sink 不该因为少一个方法
    就把整条告警链打死。问这一句本身出错也按全收处理，宁可多发不可不发。
    """
    try:
        return bool(getattr(sink, "accepts", _ALWAYS)(event))
    except Exception as exc:
        log.warning("alert.accepts_failed", sink=type(sink).__name__, error=str(exc))
        return True


def _ALWAYS(event: AlertEvent) -> bool:
    return True


_DEFAULT_STATE = {"incident": "firing", "notice": "notice"}


class AlertBus:
    """告警唯一入口。``source`` 为发布组件名（如 live-core、health-observer）。"""

    def __init__(self, sinks: Sequence[AlertSink], log: AlertEventLog | None = None, *,
                 source: str, clock: Callable[[], datetime] = now_shanghai) -> None:
        self._sinks = tuple(sinks)
        self._log = log
        self._source = source
        self._clock = clock

    def _build(self, key, subject, params, detail, evidence, severity, state) -> AlertEvent:
        spec = lookup(key)
        params = dict(params or {})
        if spec is not None:
            if severity is None and len(spec.severities) == 1:
                severity = next(iter(spec.severities))
            state = state or _DEFAULT_STATE[spec.kind]
            state_ok = (state == "notice") == (spec.kind == "notice")
            if severity in spec.severities and state_ok:
                return AlertEvent(
                    event_id=new_event_id(), key=key, source=self._source, subject=subject,
                    severity=severity, state=state, title=spec.title.format(**params), detail=detail,
                    evidence=dict(evidence or {}), occurred_at=self._clock())
        # 未登记的键、不允许的级别或状态：不丢告警，按 critical 以 unregistered: 键发出
        log.error("alert.unregistered", key=key, severity=str(severity), state=state)
        return AlertEvent(
            event_id=new_event_id(), key=f"unregistered:{key}", source=self._source, subject=subject,
            severity=Severity.CRITICAL, state="firing",
            title=str(params.get("title") or (spec.title if spec else key)), detail=detail,
            evidence=dict(evidence or {}), occurred_at=self._clock())

    def emit(self, key: str, *, subject: str = "", params: dict | None = None, detail: str = "",
             evidence: dict | None = None, severity: Severity | None = None,
             state: str | None = None) -> AlertEvent:
        try:
            event = self._build(key, subject, params, detail, evidence, severity, state)
        except Exception as exc:  # 构造失败（如模板参数缺失）也不能丢告警、不能影响主流程
            log.error("alert.build_failed", key=key, error=str(exc))
            event = AlertEvent(
                event_id=new_event_id(), key=f"unregistered:{key}", source=self._source, subject=subject,
                severity=Severity.CRITICAL, state="firing", title=str((params or {}).get("title") or key),
                detail=detail, occurred_at=now_shanghai())
        delivered, failed = 0, []
        for sink in self._sinks:
            if not _accepts(sink, event):
                continue
            try:
                sink.send(event)
                delivered += 1
            except Exception as exc:  # 一个 sink 挂了不能影响其它 sink 与主流程
                failed.append(type(sink).__name__)
                log.error("alert.sink_failed", sink=type(sink).__name__, error=str(exc))
        event = event.model_copy(update={"delivered": delivered, "failed": tuple(failed)})
        if self._log is not None:
            self._log.append(event)
        return event


def delivery_records(root, day) -> list[dict]:
    """当日全部告警的投递记录：结构化事件（计划 03 起）+ 旧格式账本（迁移前与 N-1 生产者）。

    两者写在不同文件（``.events.jsonl`` / ``.jsonl``），合并即可，不会重复。
    记录形状与旧账本行相同（at / severity / title / delivered / failed），事件额外带 key。
    """
    base = Path(root) if root else LEDGER_DIR
    records = [{"at": e.occurred_at.isoformat(timespec="seconds"), "severity": e.severity.value,
                "title": e.title, "delivered": e.delivered, "failed": list(e.failed), "key": e.key}
               for e in AlertEventLog(base).read(day)]
    return records + LegacyLedger(base).read(day)
