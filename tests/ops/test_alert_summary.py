"""告警汇总（#662 计划 04）：按稳定键归并告警条目与按日趋势。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from qsys.ops.alert_events import SHANGHAI, AlertEventLog, Severity
from qsys.ops.alert_summary import AlertSummary, summarize, trend
from qsys.ops.alerts import AlertBus, RecordingAlertSink

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=SHANGHAI)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        self.t += timedelta(minutes=1)
        return self.t


def bus(log=None, source="health-observer"):
    clock = Clock()
    return AlertBus([RecordingAlertSink()], log, source=source, clock=clock), clock


def down(b, subject="paper"):
    return b.emit("observer.process.down", subject=subject, params={"title": f"{subject}已停止"},
                  severity=Severity.CRITICAL)


def up(b, subject="paper"):
    return b.emit("observer.process.down", subject=subject, params={"title": f"{subject}已恢复"},
                  severity=Severity.INFO, state="resolved")


def test_incident_open_then_resolved():
    b, _ = bus()
    e1 = down(b)
    [i] = summarize([e1])
    assert (i.key, i.subject, i.kind, i.state, i.count, i.severity) == (
        "observer.process.down", "paper", "incident", "open", 1, Severity.CRITICAL)
    assert i.first_at == i.last_at == e1.occurred_at and i.resolved_at is None
    e2 = up(b)
    [i] = summarize([e1, e2])
    assert i.state == "resolved" and i.resolved_at == e2.occurred_at and i.count == 1
    assert i.title == "paper已恢复" and i.severity is Severity.CRITICAL


def test_flapping_incident_counts_and_state():
    b, _ = bus()
    events = [down(b), up(b), down(b), up(b), down(b)]
    [i] = summarize(events)
    assert i.count == 3 and i.state == "open"
    assert i.first_at == events[0].occurred_at and i.last_at == events[4].occurred_at
    assert i.resolved_at is None and len(i.events) == 5


def test_notice_kind_and_counts():
    b, _ = bus(source="paper")
    events = [b.emit("strategy.run.report", subject="A", params={"title": "竞价"}) for _ in range(2)]
    [i] = summarize(events)
    assert (i.kind, i.state, i.count, i.severity) == ("notice", "notice", 2, Severity.INFO)


def test_unregistered_key_flagged():
    b, _ = bus()
    [i] = summarize([b.emit("nope.never.used", params={"title": "x"})])
    assert i.unregistered and i.kind == "unregistered" and i.state == "open"


def test_incident_spans_days_and_trend_by_local_day():
    b, clock = bus()
    e1 = down(b)
    clock.t = datetime(2026, 11, 19, 23, 50, tzinfo=SHANGHAI)  # 上海 11-19 深夜（UTC 15:51）
    e2 = up(b)
    e3 = b.emit("infra.disk.data", params={"title": "磁盘余量偏低"}, severity=Severity.WARNING)
    [disk, proc] = sorted(summarize([e1, e2, e3]), key=lambda i: i.key)
    assert proc.first_at.date() == date(2026, 11, 18) and proc.resolved_at.date() == date(2026, 11, 19)
    t = trend([e1, e2, e3], [date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)])
    assert [(d.day, d.critical, d.warning, d.info, d.resolved) for d in t] == [
        (date(2026, 11, 18), 1, 0, 0, 0), (date(2026, 11, 19), 0, 1, 0, 1), (date(2026, 11, 20), 0, 0, 0, 0)]


def test_sort_open_first_then_severity_then_recency():
    b, _ = bus()
    resolved = [down(b, "a"), up(b, "a")]
    warn = b.emit("infra.disk.data", params={"title": "w"}, severity=Severity.WARNING)
    crit_old = down(b, "b")
    crit_new = down(b, "c")
    order = [(i.subject or i.key) for i in summarize([*resolved, warn, crit_old, crit_new])]
    assert order == ["c", "b", "infra.disk.data", "a"]


def test_summary_reads_multiple_roots_and_namespaces(tmp_path):
    shared, live = tmp_path / "alerts", tmp_path / "live-alerts"
    b1, _ = bus(AlertEventLog(shared / "health-observer"))
    b2, _ = bus(AlertEventLog(live), source="live-core")
    down(b1)
    b2.emit("live.reject.fuse")
    events = AlertSummary([shared, live]).events(date(2026, 11, 17), date(2026, 11, 18))
    assert sorted(e.key for e in events) == ["live.reject.fuse", "observer.process.down"]
    assert AlertSummary([shared]).events(date(2026, 11, 19), date(2026, 11, 20)) == []
