"""结构化告警事件（#662 计划 03）：告警的唯一来源。

一条事件 = 稳定键（受管清单 ``alert_keys``）+ 来源 + 对象 + 级别 + 状态（发生 / 恢复 / 通知）
+ 标题与详情 + 结构化证据 + 带时区的时间 + 投递结果。通知文本由事件生成（``render_text``）。

事件存储与旧账本同一目录，文件名 ``<YYYY-MM-DD>.events.jsonl``，每行一个事件。
留存是旁路的旁路：写失败只记日志；读取时坏行与未知版本跳过，不能让整份文件不可读。
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

import structlog
from pydantic import AfterValidator, BaseModel, ConfigDict, JsonValue

log = structlog.get_logger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SCHEMA_VERSION = 1
# 各组件独立的告警目录（相对告警根目录）；日报合并读取
SERVICE_LEDGER_NAMESPACES = frozenset({
    "health-observer", "service-checks", "reboot-drill", "paper", "shadow-core",
})


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("occurred_at 必须带时区")
    return value


class AlertEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    event_id: str
    key: str
    source: str
    subject: str = ""
    severity: Severity
    state: Literal["firing", "resolved", "notice"]
    title: str
    detail: str = ""
    evidence: dict[str, JsonValue] = {}
    occurred_at: Annotated[datetime, AfterValidator(_aware)]
    delivered: int = 0
    failed: tuple[str, ...] = ()


def new_event_id() -> str:
    return uuid.uuid4().hex


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


def render_text(event: AlertEvent) -> str:
    """通知文本，与旧 ``Alert.as_text`` 逐字一致。"""
    stamp = event.occurred_at.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
    head = f"[{event.severity.value}] {stamp} {event.title}"
    return f"{head}\n{event.detail}" if event.detail else head


class AlertEventLog:
    """按天追加事件；日报与告警汇总由此读取。"""

    def __init__(self, root) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def append(self, event: AlertEvent) -> None:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            day = event.occurred_at.astimezone(SHANGHAI).date()
            with (self._root / f"{day:%Y-%m-%d}.events.jsonl").open("a", encoding="utf-8") as out:
                out.write(event.model_dump_json() + "\n")
        except OSError as exc:
            log.warning("alert_events.append_failed", error=str(exc))

    def read(self, day: date) -> list[AlertEvent]:
        name = f"{day:%Y-%m-%d}.events.jsonl"
        paths = [self._root / name] + [self._root / ns / name for ns in sorted(SERVICE_LEDGER_NAMESPACES)]
        out: list[AlertEvent] = []
        for path in paths:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in raw.splitlines():
                try:
                    data = json.loads(line)
                    if data.get("schema_version") != SCHEMA_VERSION:
                        raise ValueError(f"unknown schema_version {data.get('schema_version')!r}")
                    out.append(AlertEvent.model_validate(data))
                except ValueError as exc:  # 半行（进程被 kill）或未来版本：跳过该行
                    log.warning("alert_events.line_skipped", path=str(path), error=str(exc)[:200])
        return out
