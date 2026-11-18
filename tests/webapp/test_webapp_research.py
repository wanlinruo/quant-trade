"""研究侧新契约：策略、回测、研究任务的读取与下载（#662 R3，设计文档 §6.3、§8.10、§8.11）。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from qsys.ops.journal import SessionJournal
from qsys.paper.store import PaperStore
from qsys.web.app import create_app
from qsys.web.backtest_views import BacktestService
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService
from qsys.webapp.app import WebappDeps
from qsys.web.live_kill_status import LiveKillReader
from qsys.webapp.contracts.research import (
    BacktestList, BacktestPending, BacktestReport, ResearchJobList, StrategyDetail, StrategyList,
)
from qsys.web.workbench import WorkbenchStore
from tests.web.test_research_jobs import _console

SOURCE = '''
from qsys.strategy.base import Strategy
PARAMETERS = {"window": {"type": "int", "default": 20, "min": 2}}
class Demo(Strategy):
    pass
'''

STATUS = (
    "run_id,strategy,start,end,sessions,initial_value,total_value,cumulative_return,fills,buys,"
    "sells,rejects,closed_lots,gate_decisions,gate_allowed,gate_rejected\n"
    "r1,Demo,2024-01-02,2024-01-05,4,100000,99000,-0.01,2,1,1,1,1,0,0,0\n"
)


def publish_report(root: Path, name: str, *, bench_missing_day: bool = False) -> None:
    run = root / name
    run.mkdir(parents=True)
    (run / "run_status.csv").write_text(STATUS, encoding="utf-8")
    (run / "config.json").write_text(json.dumps({"cost_model": "qsys 实盘档位"}), encoding="utf-8")
    (run / "metrics.csv").write_text("metric,value\nmax_drawdown,-0.2\nbenchmark_return,0.1\n",
                                     encoding="utf-8")
    (run / "daily_nav.csv").write_text(
        "date,total_value,available_cash,position_count,daily_return\n"
        "2024-01-02,100000,100000,0,\n2024-01-03,110000,0,1,0.1\n"
        "2024-01-04,88000,0,1,-0.2\n2024-01-05,99000,0,1,0.125\n", encoding="utf-8")
    days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    if bench_missing_day:
        days.remove("2024-01-04")
    (run / "benchmark_nav.csv").write_text(
        "date,total_value,daily_return\n" + "".join(f"{d},{100 + i},0\n" for i, d in enumerate(days)),
        encoding="utf-8")
    (run / "monthly_returns.csv").write_text("month,return\n2024-01,-0.01\n", encoding="utf-8")
    (run / "fills.csv").write_text(
        "filled_at,stock,symbol,side,qty,price,fee,notional\n"
        "2024-01-03T09:31:00,600000.XSHG,600000.SH,buy,100,10.0,5.0,1000.0\n"
        "2024-01-05T14:55:00,600000.XSHG,600000.SH,sell,100,9.9,5.0,990.0\n", encoding="utf-8")
    (run / "rejects.csv").write_text("date,symbol,reason\n2024-01-04,600001.SH,涨停\n",
                                     encoding="utf-8")


@pytest.fixture
def env(warehouse, tmp_path):
    db = tmp_path / "workbench.sqlite3"
    wb, paper = WorkbenchStore(db), PaperStore(db)
    runs = tmp_path / "runs"
    runs.mkdir()
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "sample.py").write_text(SOURCE, encoding="utf-8")
    state = SimpleNamespace(wb=wb, paper=paper, runs=runs, lineage=None, research=None, core_label="影子盘")

    def client() -> TestClient:
        cfg = WebConfig()
        return TestClient(create_app(
            MonitorService(SessionJournal(warehouse), config=cfg), cfg,
            backtests=BacktestService(runs), workbench=wb, paper=paper,
            strategy_templates=templates, research_jobs=state.research,
            shadow_lineage=lambda: state.lineage, core_label=state.core_label,
            webapp=WebappDeps(live_kill=LiveKillReader(None, None))))
    state.client = client
    return state


def succeeded_backtest(env, strategy_id: str, **kw) -> str:
    job = env.wb.enqueue(strategy_id, {"start": "2024-01-02", "end": "2024-01-05", "cash": 100000,
                                       "params": {"window": 20}})
    env.wb.claim_next()
    env.wb.finish(job.id, "succeeded", run_name=job.id)
    publish_report(env.runs, job.id, **kw)
    return job.id


def test_strategies_list_stage_and_latest_backtest(env):
    s = env.wb.create_strategy("demo", SOURCE)
    env.wb.create_strategy("draft-only", SOURCE)
    bt = succeeded_backtest(env, s.id)
    c = env.client()
    body = c.get("/api/app/strategies").json()
    assert body["enabled"] and body["templates"] == ["sample.py"] and body["core_label"] == "影子盘"
    rows = {r["name"]: r for r in body["strategies"]}
    assert rows["demo"]["stage"] == "backtest" and rows["draft-only"]["stage"] == "draft"
    shared = {r["id"]: r for r in c.get("/api/app/backtests").json()["rows"]}[bt]
    assert rows["demo"]["latest_backtest"] == shared
    assert rows["demo"]["latest_backtest"]["total_return"] == pytest.approx(-0.01)
    assert rows["demo"]["latest_backtest"]["max_drawdown"] == pytest.approx(-0.2)
    assert rows["draft-only"]["latest_backtest"] is None


def test_strategy_stage_paper_and_live(env):
    s = env.wb.create_strategy("demo", SOURCE)
    bt = succeeded_backtest(env, s.id)
    acct = env.paper.create(bt, "A", 100000, date(2024, 2, 1))
    row = env.client().get("/api/app/strategies").json()["strategies"][0]
    assert (row["stage"], row["stage_label"], row["stage_detail"]) == ("paper", "模拟", "A")
    env.lineage = SimpleNamespace(strategy_id=s.id, version_id=acct.version_id)
    row = env.client().get("/api/app/strategies").json()["strategies"][0]
    assert (row["stage"], row["stage_label"]) == ("live", "影子盘")
    assert row["in_use"] is True and row["used_by"] == ["影子盘", "模拟账户 A"]


def test_strategy_detail(env):
    s = env.wb.create_strategy("demo", SOURCE)
    bt = succeeded_backtest(env, s.id)
    c = env.client()
    d = c.get(f"/api/app/strategies/{s.id}").json()
    assert d["source"] == SOURCE and d["parameters"][0]["name"] == "window"
    assert [b["id"] for b in d["backtests"]] == [bt] and len(d["versions"]) == 1
    assert c.get("/api/app/strategies/nope").status_code == 404



def test_backtest_detail_report(env):
    s = env.wb.create_strategy("demo", SOURCE)
    bt = succeeded_backtest(env, s.id)
    d = env.client().get(f"/api/app/backtests/{bt}").json()
    assert d["report"] is True and d["strategy_id"] == s.id
    assert d["summary"]["cumulative_return"] == pytest.approx(-0.01)
    assert d["metrics"]["max_drawdown"] == pytest.approx(-0.2)
    assert [p["date"] for p in d["series"]] == ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    assert d["episodes"][0]["peak_date"] == "2024-01-03" and d["episodes"][0]["recovery_date"] is None
    assert d["monthly"] == [{"month": "2024-01", "value": -0.01}]
    assert d["fills_total"] == 2 and len(d["fills"]) == 2 and len(d["rejects"]) == 1
    assert d["warnings"] == ["下单被拒 1 笔（详见 rejects.csv）"]


def test_series_keeps_missing_benchmark_as_null(env):
    s = env.wb.create_strategy("demo", SOURCE)
    bt = succeeded_backtest(env, s.id, bench_missing_day=True)
    d = env.client().get(f"/api/app/backtests/{bt}").json()
    day = d["series"][2]
    assert day["benchmark"] is None and day["excess"] is None and day["strategy"] == pytest.approx(-0.12)
    assert "1 个交易日缺基准，图上断开" in d["warnings"]


def test_backtest_detail_running_job(env):
    s = env.wb.create_strategy("demo", SOURCE)
    job = env.wb.enqueue(s.id, {"start": "2024-01-02", "end": "2024-01-05", "cash": 1, "params": {}})
    c = env.client()
    d = c.get(f"/api/app/backtests/{job.id}").json()
    assert d["report"] is False and d["job"]["status"] == "queued" and "log_tail" in d
    rows = c.get("/api/app/backtests").json()["rows"]
    assert rows[0]["status"] == "queued" and rows[0]["progress"] == 0
    assert c.get("/api/app/backtests/nope").status_code == 404





def test_endpoints_disabled_without_services(warehouse):
    cfg = WebConfig()
    c = TestClient(create_app(MonitorService(SessionJournal(warehouse), config=cfg), cfg))
    for url in ("/api/app/strategies", "/api/app/backtests", "/api/app/research-jobs"):
        r = c.get(url)
        assert r.status_code == 200 and r.json() == {"schema_version": 1, "enabled": False}
        assert r.headers["cache-control"] == "no-store"


    for url in ("/api/app/backtests/x/files/a.csv", "/api/app/research-jobs/x/files/a.csv"):
        assert c.get(url).status_code == 404


def test_research_jobs_list(env, tmp_path):
    env.research = _console(tmp_path)
    first = env.research.submit("verify-snapshot", {"purpose": "x"}).job_id
    claimed = env.research.store.claim_next()
    env.research.store.transition(claimed, "running")
    env.research.store.transition(claimed, "failed")
    body = env.client().get("/api/app/research-jobs").json()
    ResearchJobList.model_validate(body)
    assert body["enabled"] and body["submission_enabled"]
    job = body["jobs"][0]
    assert job["id"] == first and job["state"] == "failed" and job["task"] == "verify-snapshot"
    assert job["code_sha"] == "a" * 40 and job["snapshot_id"] == "close-20261013"
    assert job["timeout_seconds"] == 14_400 and job["artifacts"] == []
    assert job["created_at"].endswith("+08:00")


def test_research_responses_validate_against_contracts(env):
    s = env.wb.create_strategy("demo", SOURCE)
    bt = succeeded_backtest(env, s.id)
    running = env.wb.enqueue(s.id, {"start": "2024-01-02", "end": "2024-01-05", "cash": 1, "params": {}})
    c = env.client()
    StrategyList.model_validate(c.get("/api/app/strategies").json())
    StrategyDetail.model_validate(c.get(f"/api/app/strategies/{s.id}").json())
    BacktestList.model_validate(c.get("/api/app/backtests").json())
    BacktestReport.model_validate(c.get(f"/api/app/backtests/{bt}").json())
    BacktestPending.model_validate(c.get(f"/api/app/backtests/{running.id}").json())


def test_backtest_list_blank_status_values_are_null(env):
    run = env.runs / "cli-run"
    run.mkdir()
    (run / "run_status.csv").write_text(STATUS.replace("99000,-0.01,2,1,1,1", "99000,,2,,1,"), encoding="utf-8")
    c = env.client()
    row = {r["id"]: r for r in c.get("/api/app/backtests").json()["rows"]}["cli-run"]
    assert row["total_return"] is None and row["source"] == "cli" and row["created_at"] is None
    d = c.get("/api/app/backtests/cli-run").json()
    assert d["summary"]["buys"] is None and d["summary"]["rejects"] is None
    assert d["summary"]["generated_at"] is None and d["summary"]["cost_model"] is None


def test_backtest_file_download_via_new_route(env):
    s = env.wb.create_strategy("demo", SOURCE)
    bt = succeeded_backtest(env, s.id)
    (env.runs / bt / "state.sqlite3").write_bytes(b"x")
    c = env.client()
    names = [f["name"] for f in c.get(f"/api/app/backtests/{bt}").json()["files"]]
    assert "fills.csv" in names and "state.sqlite3" not in names
    r = c.get(f"/api/app/backtests/{bt}/files/fills.csv")
    assert r.status_code == 200 and r.content.startswith(b"filled_at,")
    assert r.headers["content-disposition"].startswith("attachment;")
    assert c.get(f"/api/app/backtests/{bt}/files/state.sqlite3").status_code == 404
    assert c.get(f"/api/app/backtests/{bt}/files/..%2Frun_status.csv").status_code == 404
    assert c.get("/api/app/backtests/..%2F..%2Fetc/files/passwd").status_code == 404


def test_research_artifact_download_checks_manifest(env, tmp_path):
    import hashlib
    env.research = _console(tmp_path)
    job_id = env.research.submit("verify-snapshot", {}).job_id
    claimed = env.research.store.claim_next()
    env.research.store.transition(claimed, "running")
    art = env.research.root / "jobs" / job_id / "artifacts"
    (art / "out").mkdir(parents=True)
    body = b"result-bytes"
    (art / "out" / "report.json").write_bytes(body)
    (art / "manifest.json").write_text(json.dumps({"files": [
        {"path": "out/report.json", "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}]}),
        encoding="utf-8")
    c = env.client()
    listed = {j["id"]: j for j in c.get("/api/app/research-jobs").json()["jobs"]}[job_id]
    assert listed["artifacts"] == [{"path": "out/report.json", "bytes": len(body),
                                    "sha256": hashlib.sha256(body).hexdigest()}]
    ok = c.get(f"/api/app/research-jobs/{job_id}/files/out/report.json")
    assert ok.status_code == 200 and ok.content == body
    assert c.get(f"/api/app/research-jobs/{job_id}/files/manifest.json").status_code == 404
    (art / "out" / "report.json").write_bytes(b"tampered!!!!")
    assert c.get(f"/api/app/research-jobs/{job_id}/files/out/report.json").status_code == 404


def test_core_label_follows_deployment_mode_even_with_explicit_deps(env):
    env.core_label = "实盘"
    assert env.client().get("/api/app/strategies").json()["core_label"] == "实盘"
