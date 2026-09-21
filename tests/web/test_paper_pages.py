from datetime import date, datetime, time, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from qsys.core.calendar import AShareCalendar
from qsys.ops.journal import SessionJournal
from qsys.ops.strategy_log import RunReport, build_record
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


def _calendar():
    today = date.today()
    return AShareCalendar([today + timedelta(days=d) for d in range(-5, 60) if d % 7 not in (3, 4)])


def make_client(warehouse, tmp_path: Path, paper_calendar=_calendar):
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
        paper=paper, paper_calendar=paper_calendar,
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


def test_paper_activation_follows_the_trading_calendar(warehouse, tmp_path):
    """C：生效日取交易日历里第一个完整交易日，不按星期几猜。"""
    client, job = make_client(warehouse, tmp_path)
    ident = client.post("/api/paper-accounts", json={
        "backtest_id": job.id, "name": "日历", "cash": 100_000,
    }).json()["id"]
    activation = date.fromisoformat(client.get(f"/api/paper-accounts/{ident}").json()["activation_date"])
    assert _calendar().is_trading_day(activation)


def test_paper_create_and_resume_refuse_without_a_calendar(warehouse, tmp_path):
    def unavailable():
        raise RuntimeError("Tushare 交易日历不可用")

    client, job = make_client(warehouse, tmp_path, paper_calendar=unavailable)
    response = client.post("/api/paper-accounts", json={
        "backtest_id": job.id, "name": "无日历", "cash": 100_000,
    })
    assert response.status_code == 503
    assert "交易日历" in response.json()["detail"]


def _account_with_run(warehouse, tmp_path, *, reasons, candidates, passed):
    client, job = make_client(warehouse, tmp_path)
    ident = client.post(
        "/api/paper-accounts",
        json={"backtest_id": job.id, "name": "运行日志", "cash": 100_000},
    ).json()["id"]
    if candidates is None:
        return client, ident
    day = date.today()
    report = RunReport()
    report.report(candidates=candidates, passed=passed, reasons=reasons)
    PaperStore(tmp_path / "workbench.sqlite3").record_run(
        ident,
        build_record(
            report,
            event_key=f"{day}:auction:09:30:00",
            session=day,
            phase="auction",
            started_at=datetime.combine(day, time(9, 30)),
            finished_at=datetime.combine(day, time(9, 30, 1)),
            cash=100_000.0,
            market_value=0.0,
            position_count=0,
            orders=0,
            rejects=0,
        ),
    )
    return client, ident


def test_paper_detail_shows_the_run_log(warehouse, tmp_path):
    client, ident = _account_with_run(
        warehouse,
        tmp_path,
        candidates=12,
        passed=0,
        reasons={"估值不达标": 12},
    )

    page = client.get(f"/paper/{ident}")

    assert "运行日志" in page.text
    assert "候选 12 只 → 通过 0 只，未下单" in page.text
    assert "估值不达标" in page.text


def test_paper_detail_run_log_empty_state(warehouse, tmp_path):
    client, ident = _account_with_run(
        warehouse, tmp_path, candidates=None, passed=None, reasons=None
    )

    page = client.get(f"/paper/{ident}")

    assert "当日没有运行记录" in page.text


def test_paper_detail_explains_marking_and_strategy_frequency(warehouse, tmp_path):
    client, ident = _account_with_run(
        warehouse, tmp_path, candidates=None, passed=None, reasons=None
    )

    page = client.get(f"/paper/{ident}")

    assert "运行与更新口径" in page.text
    assert "收益盯市" in page.text
    assert "默认约每秒更新" in page.text
    assert "午休约每 60 秒更新" in page.text
    assert "不会每秒重复执行交易逻辑" in page.text
    assert "本页面不会自动刷新" in page.text
