"""告警汇总（#662 计划 04；设计文档 §8.2）：按稳定键归并告警条目与按日趋势。纯只读。

- 条目按 (key, subject) 归并；incident 类最后一条为 firing → open，为 resolved → resolved；
  notice 类为 notice。不从时间推断恢复。
- 次数、首次、最近只算发生（firing）与通知（notice）事件；恢复单独记 resolved_at。
- 趋势按事件发生日（上海时区）计数：发生/通知事件按级别计，恢复事件单独计 resolved。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from qsys.ops.alert_events import SHANGHAI, AlertEvent, AlertEventLog, Severity
from qsys.ops.alert_keys import lookup

UNREGISTERED = "unregistered:"
_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}


@dataclass(frozen=True)
class AlertIncident:
    key: str
    subject: str
    source: str
    kind: Literal["incident", "notice", "unregistered"]
    unregistered: bool
    severity: Severity
    state: Literal["open", "resolved", "notice"]
    first_at: datetime
    last_at: datetime
    resolved_at: datetime | None
    count: int
    title: str
    detail: str
    events: tuple[AlertEvent, ...]


@dataclass(frozen=True)
class DayTrend:
    day: date
    critical: int
    warning: int
    info: int
    resolved: int


def _kind(key: str) -> str:
    if key.startswith(UNREGISTERED):
        return "unregistered"
    spec = lookup(key)
    return spec.kind if spec is not None else "unregistered"


def summarize(events: Iterable[AlertEvent]) -> list[AlertIncident]:
    groups: dict[tuple[str, str], list[AlertEvent]] = {}
    for e in sorted(events, key=lambda e: e.occurred_at):
        groups.setdefault((e.key, e.subject), []).append(e)
    out = []
    for (key, subject), items in groups.items():
        happened = [e for e in items if e.state != "resolved"] or items
        last = items[-1]
        kind = _kind(key)
        if kind == "notice":
            state = "notice"
        else:
            state = "resolved" if last.state == "resolved" else "open"
        out.append(AlertIncident(
            key=key, subject=subject, source=last.source, kind=kind,  # type: ignore[arg-type]
            unregistered=kind == "unregistered",
            severity=max((e.severity for e in happened), key=_RANK.__getitem__),
            state=state, first_at=happened[0].occurred_at, last_at=happened[-1].occurred_at,  # type: ignore[arg-type]
            resolved_at=last.occurred_at if state == "resolved" else None,
            count=sum(1 for e in items if e.state != "resolved"), title=last.title, detail=last.detail,
            events=tuple(items)))
    order = {"open": 0, "notice": 1, "resolved": 2}
    return sorted(out, key=lambda i: (order[i.state], -_RANK[i.severity], -i.last_at.timestamp()))


def trend(events: Iterable[AlertEvent], days: list[date]) -> list[DayTrend]:
    counts = {d: {"critical": 0, "warning": 0, "info": 0, "resolved": 0} for d in days}
    for e in events:
        day = e.occurred_at.astimezone(SHANGHAI).date()
        if day not in counts:
            continue
        bucket = "resolved" if e.state == "resolved" else e.severity.value.lower()
        counts[day][bucket] += 1
    return [DayTrend(d, **counts[d]) for d in days]


class AlertSummary:
    """从一个或多个告警根目录（各含组件命名空间）读取事件。"""

    def __init__(self, roots: Sequence[Path]) -> None:
        self._logs = [AlertEventLog(Path(r)) for r in dict.fromkeys(Path(r) for r in roots)]

    def events(self, start: date, end: date) -> list[AlertEvent]:
        out: list[AlertEvent] = []
        day = start
        while day <= end:
            for log in self._logs:
                out.extend(log.read(day))
            day += timedelta(days=1)
        return out
