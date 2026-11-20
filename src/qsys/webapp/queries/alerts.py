"""告警中心查询（#662 计划 06a；设计文档 §8.2、§13 第 1、2 项）。

事件来自计划 03 的事件存储（多个根目录，各含组件命名空间），经计划 04 的 alert_summary 归并；
“已知悉”来自 Web 自有存储。条目状态：
- 通知类 → notice；已恢复 → resolved；
- 未恢复：确认的 through 覆盖最近一次发生 → acknowledged，否则 unhandled（确认后再次发生回到未处理）。
告警目录都不可读时 state=unknown，计数为 None，不显示为 0 条。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path

from qsys.ops.alert_events import SHANGHAI, now_shanghai
from qsys.ops.alert_keys import lookup
from qsys.ops.alert_summary import AlertIncident, AlertSummary, summarize, trend
from qsys.webapp.cache import SingleFlightCache
from qsys.webapp.contracts.alerts import (
    AckInfo, AlertCenter, AlertDetail, AlertEventInfo, AlertItem, AlertTrendDay,
)
from qsys.webapp.stores.alert_acks import AlertAckStore

WINDOW_DAYS = 14
TTL_SECONDS = 60.0


def _readable(root: Path) -> bool:
    return root.is_dir() and os.access(root, os.R_OK | os.X_OK)


class AlertCenterQuery:
    def __init__(self, roots: Sequence[Path], acks: AlertAckStore | None, *,
                 clock: Callable[[], datetime] = now_shanghai) -> None:
        self._roots = tuple(Path(r) for r in roots)
        self.acks = acks
        self._clock = clock
        self._caches: dict[date | None, SingleFlightCache] = {}

    def clear(self) -> None:
        self._caches.clear()

    def _window(self, day: date | None) -> tuple[date, date, datetime | None]:
        end = day or self._clock().astimezone(SHANGHAI).date()
        until = datetime.combine(end + timedelta(days=1), time(), SHANGHAI) if day else None
        return end - timedelta(days=WINDOW_DAYS - 1), end, until

    def _incidents(self, day: date | None):
        start, end, until = self._window(day)
        readable = [r for r in self._roots if _readable(r)]
        events = AlertSummary(readable).events(start, end) if readable else []
        acks = self.acks.latest(until=until) if self.acks is not None else {}
        return start, end, readable, events, summarize(events), acks

    def _item(self, i: AlertIncident, acks) -> AlertItem:
        record = acks.get((i.key, i.subject))
        if i.kind == "notice":
            status = "notice"
        elif i.state == "resolved":
            status = "resolved"
        elif record is not None and record.through >= i.last_at:
            status = "acknowledged"
        else:
            status = "unhandled"
        spec = lookup(i.key)
        return AlertItem(
            key=i.key, subject=i.subject, source=i.source, kind=i.kind, severity=i.severity.value,
            status=status, first_at=i.first_at, last_at=i.last_at, resolved_at=i.resolved_at, count=i.count,
            title=i.title, detail=i.detail, description=spec.description if spec else None,
            ack=None if record is None else AckInfo(by=record.by, acked_at=record.acked_at, through=record.through))

    def _build(self, day: date | None) -> AlertCenter:
        start, end, readable, events, incidents, acks = self._incidents(day)
        items = [self._item(i, acks) for i in incidents]
        days = [start + timedelta(days=n) for n in range(WINDOW_DAYS)]
        missing: dict[str, str] = {}
        if not self.acks:
            missing["ack"] = "“已知悉”存储未配置"
        if not readable:
            missing["alerts"] = "告警存储不可读或未配置"
            return AlertCenter(state="unknown", day=end, window_start=start, window_end=end, unhandled=None,
                               unhandled_critical=None, acknowledged=None, notices=None, items=[],
                               trend=[], missing=missing)
        if len(readable) < len(self._roots):
            missing["alerts_partial"] = f"{len(self._roots) - len(readable)} 个告警目录不可读"
        unhandled = [i for i in items if i.status == "unhandled"]
        return AlertCenter(
            state="ok", day=end, window_start=start, window_end=end, unhandled=len(unhandled),
            unhandled_critical=sum(i.severity == "CRITICAL" for i in unhandled),
            acknowledged=sum(i.status == "acknowledged" for i in items),
            notices=sum(i.status == "notice" for i in items), items=items,
            trend=[AlertTrendDay(day=t.day, critical=t.critical, warning=t.warning, info=t.info, resolved=t.resolved)
                   for t in trend(events, days)],
            missing=missing)

    def center(self, day: date | None) -> AlertCenter:
        if day not in self._caches:
            self._caches[day] = SingleFlightCache(lambda: self._build(day), TTL_SECONDS)
        return self._caches[day].get()

    def detail(self, key: str, subject: str, day: date | None) -> AlertDetail | None:
        _, _, _, _, incidents, acks = self._incidents(day)
        match = next((i for i in incidents if i.key == key and i.subject == subject), None)
        if match is None:
            return None
        return AlertDetail(item=self._item(match, acks), events=[
            AlertEventInfo(event_id=e.event_id, occurred_at=e.occurred_at, severity=e.severity.value, state=e.state,
                           title=e.title, detail=e.detail, evidence=dict(e.evidence), delivered=e.delivered,
                           failed=list(e.failed)) for e in match.events])

    def unhandled_count(self) -> int | None:
        try:
            return self.center(None).unhandled
        except Exception:  # noqa: BLE001 —— 顶栏计数读不到就是未知
            return None
