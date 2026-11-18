"""告警“确认已知悉”写接口（#662 计划 06a）：只记录，不改变告警与业务状态。"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from qsys.ops.alert_events import SHANGHAI, AlertEventLog, Severity
from qsys.ops.alerts import AlertBus, RecordingAlertSink
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.live_kill_status import LiveKillReader
from qsys.web.views import MonitorService
from qsys.webapp.app import WebappDeps
from qsys.webapp.stores.alert_acks import AlertAckStore


def fire(root):
    at = datetime.now(SHANGHAI).replace(microsecond=0) - timedelta(minutes=5)
    bus = AlertBus([RecordingAlertSink()], AlertEventLog(root), source="health-observer", clock=lambda: at)
    return bus.emit("observer.process.down", subject="paper", params={"title": "模拟交易已停止"},
                    severity=Severity.CRITICAL)


def client(warehouse, tmp_path, acks=True):
    cfg = WebConfig()
    deps = WebappDeps(live_kill=LiveKillReader(None, None), alert_roots=(tmp_path / "alerts",),
                      alert_acks=tmp_path / "acks.sqlite3" if acks else None)
    return TestClient(create_app(MonitorService(SessionJournal(warehouse), config=cfg), cfg, webapp=deps))


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_ack_records_who_and_through(warehouse, tmp_path):
    e = fire(tmp_path / "alerts")
    c = client(warehouse, tmp_path)
    body = {"key": "observer.process.down", "subject": "paper", "through": e.occurred_at.isoformat()}
    r = c.post("/api/app/alerts/ack", json=body, headers=basic("维护者", "s3cret"))
    assert r.status_code == 201 and r.json()["ack"]["by"] == "维护者"
    item = next(i for i in c.get("/api/app/alerts").json()["items"] if i["subject"] == "paper")
    assert item["status"] == "acknowledged" and item["ack"]["by"] == "维护者"
    r2 = c.post("/api/app/alerts/ack", json=body)
    assert r2.status_code == 201 and r2.json()["ack"]["by"] == "unknown"
    assert list(AlertAckStore(tmp_path / "acks.sqlite3").latest().values())[0].by == "unknown"


def test_ack_validation(warehouse, tmp_path):
    e = fire(tmp_path / "alerts")
    c = client(warehouse, tmp_path)
    ok = {"key": "observer.process.down", "subject": "paper", "through": e.occurred_at.isoformat()}
    assert c.post("/api/app/alerts/ack", json={**ok, "evil": 1}).status_code == 422
    assert c.post("/api/app/alerts/ack", json={**ok, "through": "bad"}).status_code == 422
    nope = c.post("/api/app/alerts/ack", json={**ok, "subject": "nope"})
    assert nope.status_code == 404 and nope.json()["error"]["code"] == "http_404"
    stale = c.post("/api/app/alerts/ack", json={**ok, "through": (e.occurred_at - timedelta(minutes=1)).isoformat()})
    assert stale.status_code == 409
    assert AlertAckStore(tmp_path / "acks.sqlite3").latest() == {}


def test_ack_route_absent_without_store(warehouse, tmp_path):
    e = fire(tmp_path / "alerts")
    c = client(warehouse, tmp_path, acks=False)
    r = c.post("/api/app/alerts/ack", json={"key": "observer.process.down", "subject": "paper",
                                            "through": e.occurred_at.isoformat()})
    assert r.status_code in (404, 405)


def test_ack_never_logs_password(warehouse, tmp_path, capsys, caplog):
    e = fire(tmp_path / "alerts")
    c = client(warehouse, tmp_path)
    c.post("/api/app/alerts/ack", json={"key": "observer.process.down", "subject": "paper",
                                        "through": e.occurred_at.isoformat()}, headers=basic("维护者", "TopSecret42"))
    out = capsys.readouterr()
    assert "TopSecret42" not in out.out + out.err + caplog.text
    assert b"TopSecret42" not in (tmp_path / "acks.sqlite3").read_bytes()
