"""告警中心对外契约（#662 计划 06a；设计文档 §8.2）。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from qsys.webapp.contracts.base import Contract, Timestamp

AlertSeverity = Literal["INFO", "WARNING", "CRITICAL"]


class AlertsDisabled(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[False] = False


class AckInfo(Contract):
    by: str
    acked_at: Timestamp
    through: Timestamp


class AlertItem(Contract):
    key: str
    subject: str
    source: str
    kind: Literal["incident", "notice", "unregistered"]
    severity: AlertSeverity
    status: Literal["unhandled", "acknowledged", "resolved", "notice"]
    first_at: Timestamp
    last_at: Timestamp
    resolved_at: Timestamp | None
    count: int
    title: str
    detail: str
    description: str | None  # 受管键清单中的说明；未登记键为 None
    ack: AckInfo | None


class AlertTrendDay(Contract):
    day: date
    critical: int
    warning: int
    info: int
    resolved: int


class AlertCenter(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    state: Literal["ok", "unknown"]
    day: date
    window_start: date
    window_end: date
    unhandled: int | None
    unhandled_critical: int | None
    acknowledged: int | None
    notices: int | None
    items: list[AlertItem]
    trend: list[AlertTrendDay]
    missing: dict[str, str]


class AlertEventInfo(Contract):
    event_id: str
    occurred_at: Timestamp
    severity: AlertSeverity
    state: Literal["firing", "resolved", "notice"]
    title: str
    detail: str
    evidence: dict[str, JsonValue]
    delivered: int
    failed: list[str]


class AlertDetail(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    item: AlertItem
    events: list[AlertEventInfo]


class AckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    subject: str = ""
    through: Timestamp


class AckResult(Contract):
    schema_version: Literal[1] = 1
    ack: AckInfo
