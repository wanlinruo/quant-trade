"""研究侧写接口（#662 R3）：只在服务启用时注册；请求体 extra=forbid；领域校验返回 422/409/403。"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService
from tests.web.test_research_jobs import _console
from tests.webapp.test_webapp_research import SOURCE, env, succeeded_backtest  # noqa: F401

WORKER_KEYS = {"start", "end", "cash", "jq_compat", "params", "entry_fill"}


def test_create_and_save_strategy(env):
    c = env.client()
    r = c.post("/api/app/strategies", json={"name": "新策略", "source": SOURCE})
    assert r.status_code == 201 and r.json()["schema_version"] == 1
    sid = r.json()["id"]
    assert env.wb.strategy(sid).name == "新策略"
    ok = c.put(f"/api/app/strategies/{sid}", json={"name": "改名", "source": SOURCE + "\n# x\n"})
    assert ok.status_code == 200 and ok.json() == {"schema_version": 1, "id": sid}
    bad = c.post("/api/app/strategies", json={"name": "x", "source": "def ("})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "http_422"
    extra = c.post("/api/app/strategies", json={"name": "x", "source": SOURCE, "evil": 1})
    assert extra.status_code == 422 and extra.json()["error"]["code"] == "invalid_request"
    assert c.put("/api/app/strategies/nope", json={"name": "x", "source": SOURCE}).status_code == 404


def test_save_in_use_strategy_is_rejected(env):
    s = env.wb.create_strategy("demo", SOURCE)
    bt = succeeded_backtest(env, s.id)
    env.paper.create(bt, "A", 100000, date(2024, 2, 1))
    r = env.client().put(f"/api/app/strategies/{s.id}", json={"name": "demo", "source": SOURCE + "\n# x\n"})
    assert r.status_code == 409
    message = r.json()["error"]["message"]
    assert r.json()["error"]["code"] == "http_409" and "模拟账户 A" in message and "另存" in message
    assert env.wb.strategy(s.id).draft == SOURCE


def test_import_template_rejects_path(env):
    c = env.client()
    r = c.post("/api/app/strategies/import", json={"filename": "sample.py"})
    assert r.status_code == 201 and env.wb.strategy(r.json()["id"]) is not None
    for name in ("../sample.py", "sample.txt", "", "a/sample.py"):
        assert c.post("/api/app/strategies/import", json={"filename": name}).status_code == 422, name
    assert c.post("/api/app/strategies/import", json={"filename": "nope.py"}).status_code == 404


def _req(sid, **over):
    return {"strategy_id": sid, "start": "2024-01-02", "end": "2024-01-05", "cash": 100000,
            "params": {"window": 20}, **over}


def test_backtest_enqueue_validation(env):
    s = env.wb.create_strategy("demo", SOURCE)
    c = env.client()
    for body in (_req(s.id, evil=1), _req(s.id, start="2024-02-01"), _req(s.id, cash=0),
                 _req(s.id, entry_fill="magic"), _req(s.id, params={"window": 1}), _req(s.id, start="x")):
        r = c.post("/api/app/backtests", json=body)
        assert r.status_code == 422 and set(r.json()) == {"error"}, body
    assert c.post("/api/app/backtests", json=_req("nope")).status_code == 404
    assert env.wb.jobs() == []
    ok = c.post("/api/app/backtests", json=_req(s.id))
    assert ok.status_code == 201
    jid = ok.json()["id"]
    assert c.post(f"/api/app/backtests/{jid}/cancel").json() == {"schema_version": 1, "status": "cancelled"}
    assert c.post(f"/api/app/backtests/{jid}/cancel").status_code == 409


def test_backtest_enqueue_config_matches_worker_contract(env):
    s = env.wb.create_strategy("demo", SOURCE)
    jid = env.client().post("/api/app/backtests", json=_req(s.id, jq_compat=True, entry_fill="open")).json()["id"]
    config = dict(env.wb.job(jid).config)
    assert set(config) == WORKER_KEYS
    assert config == {"start": "2024-01-02", "end": "2024-01-05", "cash": 100000.0, "jq_compat": True,
                      "params": {"window": 20}, "entry_fill": "open"}


def test_research_job_submit_cancel(env, tmp_path):
    env.research = _console(tmp_path)
    c = env.client()
    r = c.post("/api/app/research-jobs", json={"task": "verify-snapshot", "parameters": {}, "timeout_seconds": 600})
    assert r.status_code == 201
    jid = r.json()["id"]
    assert c.post(f"/api/app/research-jobs/{jid}/cancel").json() == {
        "schema_version": 1, "status": "cancellation-requested"}
    assert c.post("/api/app/research-jobs/nope/cancel").status_code == 404
    assert c.post("/api/app/research-jobs", json={"task": "verify-snapshot", "parameters": []}).status_code == 422


def test_no_write_routes_without_services(warehouse):
    cfg = WebConfig()
    c = TestClient(create_app(MonitorService(SessionJournal(warehouse), config=cfg), cfg))
    writes = [("post", "/api/app/strategies"), ("put", "/api/app/strategies/x"),
              ("post", "/api/app/strategies/import"), ("post", "/api/app/backtests"),
              ("post", "/api/app/backtests/x/cancel"), ("post", "/api/app/research-jobs"),
              ("post", "/api/app/research-jobs/x/cancel"), ("post", "/api/app/research-jobs/x/resubmit")]
    for method, url in writes:
        assert getattr(c, method)(url, json={}).status_code in (404, 405), url

def test_research_jobs_list_and_resubmit_creates_new_job_and_keeps_failed(env, tmp_path):
    env.research = _console(tmp_path)
    c = env.client()
    first = c.post("/api/app/research-jobs", json={"task": "verify-snapshot", "parameters": {"purpose": "x"}}).json()["id"]
    claimed = env.research.store.claim_next()
    env.research.store.transition(claimed, "running")
    env.research.store.transition(claimed, "failed")
    body = c.get("/api/app/research-jobs").json()
    assert body["enabled"] and body["submission_enabled"]
    job = body["jobs"][0]
    assert job["id"] == first and job["state"] == "failed" and job["task"] == "verify-snapshot"
    assert job["code_sha"] == "a" * 40 and job["snapshot_id"] == "close-20261013"
    assert job["timeout_seconds"] == 14_400 and job["artifacts"] == []
    r = c.post(f"/api/app/research-jobs/{first}/resubmit")
    assert r.status_code == 201
    new = r.json()["id"]
    assert new != first
    states = {j["id"]: j for j in c.get("/api/app/research-jobs").json()["jobs"]}
    assert states[first]["state"] == "failed"
    assert states[new]["state"] == "queued" and states[new]["parameters"] == {"purpose": "x"}



def test_resubmit_rejected_when_submission_disabled_or_job_active(env, tmp_path):
    env.research = _console(tmp_path)
    c = env.client()
    active = c.post("/api/app/research-jobs", json={"task": "verify-snapshot", "parameters": {}}).json()["id"]
    assert c.post(f"/api/app/research-jobs/{active}/resubmit").status_code == 409
    assert c.post("/api/app/research-jobs/nope/resubmit").status_code == 404
    from qsys.research.job_console import ResearchJobConsole
    env.research = ResearchJobConsole(env.research.root)
    ro = env.client()
    assert ro.get("/api/app/research-jobs").json()["submission_enabled"] is False
    assert ro.post(f"/api/app/research-jobs/{active}/resubmit").status_code == 403

