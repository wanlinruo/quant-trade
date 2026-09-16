from pathlib import Path

from fastapi.testclient import TestClient

from qsys.ops.journal import SessionJournal
from qsys.paper.store import PaperStore
from qsys.web.app import create_app
from qsys.web.backtest_views import BacktestService
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService
from qsys.web.workbench import WorkbenchStore


SOURCE = """
from qsys.strategy.base import Strategy
class Demo(Strategy):
    pass
"""


def make_client(warehouse, tmp_path: Path):
    path = tmp_path / "workbench.sqlite3"
    workbench, paper = WorkbenchStore(path), PaperStore(path)
    strategy = workbench.create_strategy("demo", SOURCE)
    job = workbench.enqueue(strategy.id, {
        "start": "2026-01-01", "end": "2026-02-01", "cash": 100_000,
        "params": {}, "jq_compat": False,
    })
    workbench.claim_next()
    workbench.finish(job.id, "succeeded", run_name=job.id)
    run = tmp_path / "runs" / job.id
    run.mkdir(parents=True)
    (run / "run_status.csv").write_text(
        "run_id,strategy,start,end,initial_value,total_value,cumulative_return\n"
        "run-1,Demo,2026-01-01,2026-02-01,100000,110000,0.1\n",
        encoding="utf-8",
    )
    (run / "config.json").write_text(
        '{"cost_model":"qsys","generated_at":"2026-09-16T09:00:00"}',
        encoding="utf-8",
    )
    cfg = WebConfig()
    app = create_app(
        MonitorService(SessionJournal(warehouse), config=cfg), cfg,
        backtests=BacktestService(tmp_path / "runs"), workbench=workbench,
        paper=paper,
    )
    return TestClient(app), job


def test_successful_backtest_can_create_and_control_paper_account(warehouse, tmp_path):
    client, job = make_client(warehouse, tmp_path)
    assert "创建模拟交易" in client.get(f"/backtests/{job.id}").text
    response = client.post("/api/paper-accounts", json={
        "backtest_id": job.id, "name": "Demo 模拟", "cash": 200_000,
    })
    assert response.status_code == 201
    ident = response.json()["id"]
    assert "Demo 模拟" in client.get("/paper").text
    assert "初始资金" in client.get(f"/paper/{ident}").text
    assert client.post(f"/api/paper-accounts/{ident}/pause").json()["status"] == "paused"
    assert client.post(f"/api/paper-accounts/{ident}/resume").json()["status"] == "scheduled"
    assert client.post(f"/api/paper-accounts/{ident}/end").json()["status"] == "ended"


def test_paper_api_rejects_unknown_or_unsuccessful_backtest(warehouse, tmp_path):
    client, _ = make_client(warehouse, tmp_path)
    response = client.post("/api/paper-accounts", json={
        "backtest_id": "missing", "name": "bad", "cash": 100_000,
    })
    assert response.status_code == 409
