"""告警中心（#662 计划 06a；设计文档 §8.2、§13 第 2 项）。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from qsys.ops.alert_events import SHANGHAI, AlertEventLog, Severity
from qsys.ops.alerts import AlertBus, RecordingAlertSink
from qsys.webapp.app import WebappDeps, create_app
from qsys.webapp.config import WebConfig
from qsys.webapp.live_kill_status import LiveKillReader
from qsys.webapp.contracts.alerts import AlertCenter, AlertDetail
from qsys.webapp.queries.alerts import AlertCenterQuery
from qsys.webapp.stores.alert_acks import AlertAckStore

DAY = date(2026, 11, 18)
T0 = datetime(2026, 11, 18, 9, 0, tzinfo=SHANGHAI)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def emit(root, key, *, at, subject="", severity=None, state=None, title="t", source="health-observer"):
    bus = AlertBus([RecordingAlertSink()], AlertEventLog(root), source=source, clock=lambda: at)
    return bus.emit(key, subject=subject, params={"title": title}, severity=severity, state=state)


def down(root, at, subject="paper"):
    return emit(root, "observer.process.down", at=at, subject=subject, severity=Severity.CRITICAL,
                title=f"{subject}已停止")


def up(root, at, subject="paper"):
    return emit(root, "observer.process.down", at=at, subject=subject, severity=Severity.INFO,
                state="resolved", title=f"{subject}已恢复")


def query(tmp_path, acks=True, now=T0 + timedelta(hours=8)):
    store = AlertAckStore(tmp_path / "acks.sqlite3") if acks else None
    return AlertCenterQuery((tmp_path / "alerts",), store, clock=lambda: now), store


def items(center):
    return {(i.key, i.subject): i for i in center.items}


def test_ack_store_append_and_latest(tmp_path):
    s = AlertAckStore(tmp_path / "a.sqlite3")
    s.ack("k.a.b", "x", by="维护者", through=T0, at=T0 + timedelta(minutes=1))
    s.ack("k.a.b", "x", by="维护者2", through=T0 + timedelta(hours=1), at=T0 + timedelta(hours=1, minutes=1))
    latest = s.latest()
    assert latest[("k.a.b", "x")].by == "维护者2"
    assert s.latest(until=T0 + timedelta(minutes=30))[("k.a.b", "x")].by == "维护者"
    assert s.latest(until=T0) == {}


def test_center_counts_and_statuses(tmp_path):
    root = tmp_path / "alerts"
    down(root, T0, "paper")
    down(root, T0, "web")
    up(root, T0 + timedelta(minutes=5), "web")
    emit(root, "infra.disk.data", at=T0, severity=Severity.WARNING, title="磁盘余量偏低")
    emit(root, "strategy.run.report", at=T0, subject="A", title="竞价", source="paper")
    c = query(tmp_path)[0].center(None)
    AlertCenter.model_validate(c.model_dump(mode="json"))
    st = {k: i.status for k, i in items(c).items()}
    assert st == {("observer.process.down", "paper"): "unhandled", ("observer.process.down", "web"): "resolved",
                  ("infra.disk.data", ""): "unhandled", ("strategy.run.report", "A"): "notice"}
    assert (c.state, c.unhandled, c.unhandled_critical, c.acknowledged, c.notices) == ("ok", 2, 1, 0, 1)
    assert items(c)[("observer.process.down", "paper")].description == "服务进程停止"


def test_refire_after_ack_is_unhandled_again(tmp_path):
    root = tmp_path / "alerts"
    first = down(root, T0)
    q, store = query(tmp_path)
    store.ack("observer.process.down", "paper", by="维护者", through=first.occurred_at, at=T0 + timedelta(minutes=1))
    item = items(q.center(None))[("observer.process.down", "paper")]
    assert item.status == "acknowledged" and item.ack.by == "维护者"
    up(root, T0 + timedelta(minutes=2))
    down(root, T0 + timedelta(minutes=3))
    item = items(query(tmp_path)[0].center(None))[("observer.process.down", "paper")]
    assert item.status == "unhandled" and item.ack.by == "维护者" and item.count == 2


def test_unregistered_counts_as_critical_unhandled(tmp_path):
    emit(tmp_path / "alerts", "nope.never.used", at=T0, title="怪告警")
    c = query(tmp_path)[0].center(None)
    [i] = c.items
    assert i.kind == "unregistered" and i.status == "unhandled" and i.severity == "CRITICAL"
    assert c.unhandled_critical == 1 and i.description is None


def test_unreadable_roots_report_unknown_not_zero(tmp_path):
    q = AlertCenterQuery((tmp_path / "missing",), None, clock=lambda: T0)
    c = q.center(None)
    assert c.state == "unknown" and c.unhandled is None and c.unhandled_critical is None
    assert "alerts" in c.missing and q.unhandled_count() is None


def test_day_view_excludes_later_events_and_acks(tmp_path):
    root = tmp_path / "alerts"
    e = down(root, T0)
    q, store = query(tmp_path, now=T0 + timedelta(days=3))
    store.ack("observer.process.down", "paper", by="维护者", through=e.occurred_at, at=T0 + timedelta(days=1, hours=2))
    down(root, T0 + timedelta(days=2), "web")
    past = q.center(DAY)
    assert (past.day, past.window_start, past.window_end) == (DAY, DAY - timedelta(days=13), DAY)
    assert set(items(past)) == {("observer.process.down", "paper")}
    assert items(past)[("observer.process.down", "paper")].status == "unhandled"  # 确认发生在该日之后
    today = q.center(None)
    assert items(today)[("observer.process.down", "paper")].status == "acknowledged"


def test_trend_14_days(tmp_path):
    root = tmp_path / "alerts"
    down(root, T0)
    up(root, T0 + timedelta(hours=1))
    trend = query(tmp_path, now=T0)[0].center(None).trend
    assert len(trend) == 14 and trend[-1].day == DAY and (trend[-1].critical, trend[-1].resolved) == (1, 1)


def test_detail_timeline_and_404(tmp_path):
    root = tmp_path / "alerts"
    down(root, T0)
    up(root, T0 + timedelta(minutes=5))
    q, _ = query(tmp_path)
    d = q.detail("observer.process.down", "paper", None)
    AlertDetail.model_validate(d.model_dump(mode="json"))
    assert [e.state for e in d.events] == ["firing", "resolved"] and d.item.status == "resolved"
    assert q.detail("observer.process.down", "nope", None) is None


@pytest.fixture
def client(warehouse, tmp_path):
    def make(acks=True):
        cfg = WebConfig()
        deps = WebappDeps(live_kill=LiveKillReader(None, None), alert_roots=(tmp_path / "alerts",),
                          alert_acks=tmp_path / "acks.sqlite3" if acks else None)
        return TestClient(create_app(deps, cfg))
    return make


def test_alert_routes(client, tmp_path):
    down(tmp_path / "alerts", datetime.now(SHANGHAI) - timedelta(minutes=5))
    c = client()
    body = c.get("/api/app/alerts").json()
    assert body["enabled"] and body["unhandled"] == 1
    assert c.get("/api/app/alerts", params={"day": "bad"}).status_code == 422
    d = c.get("/api/app/alerts/detail", params={"key": "observer.process.down", "subject": "paper"})
    assert d.status_code == 200 and d.headers["cache-control"] == "no-store"
    assert c.get("/api/app/alerts/detail", params={"key": "x.y.z", "subject": ""}).status_code == 404


def test_shell_alerts_open_from_query(client, tmp_path):
    down(tmp_path / "alerts", datetime.now(SHANGHAI) - timedelta(minutes=5))
    shell = client().get("/api/app/shell/status").json()
    assert shell["alerts_open"] == 1 and "alerts_open" not in shell["missing"]


def test_shell_alerts_open_unknown_without_roots(warehouse):
    cfg = WebConfig()
    c = TestClient(create_app(WebappDeps(live_kill=LiveKillReader(None, None)), cfg))
    shell = c.get("/api/app/shell/status").json()
    assert shell["alerts_open"] is None and shell["missing"]["alerts_open"] == "告警存储未配置"
    assert c.get("/api/app/alerts").json() == {"schema_version": 1, "enabled": False}


def test_monitor_cli_wires_alert_roots_and_acks(tmp_path, monkeypatch):
    from qsys.live.config import DeployConfig
    import qsys.live.config as live_config
    from qsys.webapp import cli as monitor_cli
    monkeypatch.setattr(live_config, "load_deploy_config", lambda _: DeployConfig())
    captured = {}
    monkeypatch.setattr(monitor_cli, "create_app", lambda *a, **kw: captured.update(args=a, kwargs=kw))
    monitor_cli.build_app(tmp_path / "warehouse", WebConfig(), None, workspace_dir=tmp_path / "workspace",
                          alert_roots=(tmp_path / "a", tmp_path / "b"), alert_acks=tmp_path / "acks.sqlite3")
    deps = captured["args"][0]
    assert deps.alert_roots == (tmp_path / "a", tmp_path / "b") and deps.alert_acks == tmp_path / "acks.sqlite3"
