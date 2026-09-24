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


def make_client(warehouse, tmp_path: Path, paper_calendar=_calendar, config=None,
                lineage=None):
    """lineage：返回承袭信息的无参函数（模拟 run_monitor 传进来的那个）。"""
    path = tmp_path / "workbench.sqlite3"
    workbench, paper = WorkbenchStore(path), PaperStore(path)
    strategy = workbench.create_strategy("demo", SOURCE)
    job = workbench.enqueue(strategy.id, config or {
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
        shadow_lineage=lineage,
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


def _account_with_run(warehouse, tmp_path, *, reasons, candidates, passed, routed=None):
    client, job = make_client(warehouse, tmp_path)
    ident = client.post(
        "/api/paper-accounts",
        json={"backtest_id": job.id, "name": "运行日志", "cash": 100_000},
    ).json()["id"]
    if candidates is None:
        return client, ident
    day = date.today()
    report = RunReport()
    report.report(candidates=candidates, routed=routed, passed=passed, reasons=reasons)
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
    assert "候选 12 只 → 闸门通过 0 只，未下单" in page.text
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


# ---- 收益曲线：历史与分时 ----

def _account_with_equity(warehouse, tmp_path):
    from qsys.core.portfolio import Portfolio

    client, job = make_client(warehouse, tmp_path)
    ident = client.post(
        "/api/paper-accounts",
        json={"backtest_id": job.id, "name": "曲线", "cash": 100_000},
    ).json()["id"]
    store = PaperStore(tmp_path / "workbench.sqlite3")
    for day, closes in ((date(2026, 9, 18), (100_000, 100_000)),
                        (date(2026, 9, 21), (99_000, 100_050))):
        opened = datetime.combine(day, time(9, 30))
        key = f"{day}:auction:09:30:00"
        store.begin_event(ident, key, opened, "auction")
        store.commit_event(ident, key, Portfolio(100_000), {}, {}, at=opened)
        for at, total in ((time(10, 0), closes[0]), (time(15, 0), closes[1])):
            store.save_snapshot(ident, Portfolio(total), {}, {},
                                at=datetime.combine(day, at), benchmark_value=100_100)
    return client, ident


def test_paper_detail_defaults_to_the_daily_history(warehouse, tmp_path):
    client, ident = _account_with_equity(warehouse, tmp_path)

    page = client.get(f"/paper/{ident}")

    assert 'data-view="history"' in page.text
    assert "累计收益（每日收盘）" in page.text
    assert '"session": "2026-09-18"' in page.text and '"session": "2026-09-21"' in page.text
    assert "paper-chart" not in page.text


def test_paper_detail_shows_the_selected_day_intraday(warehouse, tmp_path):
    client, ident = _account_with_equity(warehouse, tmp_path)

    page = client.get(f"/paper/{ident}?chart_day=2026-09-21")

    assert 'data-view="intraday"' in page.text
    assert "2026-09-21 当日收益（相对前一交易日收盘）" in page.text
    assert '"time": "10:00"' in page.text


def test_paper_detail_unknown_chart_day_falls_back_to_history(warehouse, tmp_path):
    client, ident = _account_with_equity(warehouse, tmp_path)

    page = client.get(f"/paper/{ident}?chart_day=2026-01-05")

    assert 'data-view="history"' in page.text
    assert "2026-01-05 没有收益记录，已显示历史曲线" in page.text


def test_paper_detail_without_equity_shows_the_empty_state(warehouse, tmp_path):
    client, ident = _account_with_run(
        warehouse, tmp_path, candidates=None, passed=None, reasons=None
    )

    page = client.get(f"/paper/{ident}")

    assert "模拟盘生效后将在这里展示收益曲线" in page.text


def test_paper_detail_shows_its_strategy_backtest_and_parameters(warehouse, tmp_path):
    """做对比要知道这个账户是哪套参数、从哪次回测来的。"""
    client, job = make_client(warehouse, tmp_path, config={
        "start": "2026-01-01", "end": "2026-09-22", "cash": 100_000, "jq_compat": False,
        "entry_fill": "continuous",
        "params": {"require_auction_volume": True, "auction_at": "09:25:10",
                   "entry_price_buffer": 0.03, "sizing_price_buffer": 0.005},
    })
    ident = client.post("/api/paper-accounts", json={
        "backtest_id": job.id, "name": "Demo 模拟", "cash": 100_000,
    }).json()["id"]

    body = client.get(f"/paper/{ident}").text

    assert "策略与来源" in body
    assert job.id in body and job.strategy_id in body
    assert "2026-01-01 至 2026-09-22" in body
    assert "连续竞价判定" in body
    assert "竞价决策时刻" in body and "09:25:10" in body
    assert "保护价上浮" in body and "3%（开盘价 ×1.03）" in body


class _Lineage:
    """影子核心承袭的模拟账户（只取页面要用的字段）。"""

    def __init__(self, account_id, **kw):
        self.paper_account_id = account_id
        self.paper_account_name = kw.get("name", "模拟-09:25入场")
        self.paper_status = "running"
        self.backtest_id = kw.get("backtest_id", "bt-9")
        self.strategy_name = "structural_v1_2"
        self.version_id = "v1"
        self.params = {"auction_at": "09:25:10", "entry_price_buffer": 0.03}


def test_paper_chart_overlays_the_shadow_only_when_it_follows_this_account(
    warehouse, tmp_path,
):
    """影子盘承袭的是哪个账户，就只在那个账户页上叠加它的曲线。"""
    follows = {"lineage": None}
    client, job = make_client(warehouse, tmp_path, lineage=lambda: follows["lineage"])
    mine = client.post("/api/paper-accounts", json={
        "backtest_id": job.id, "name": "跟随的账户", "cash": 100_000,
    }).json()["id"]
    other = client.post("/api/paper-accounts", json={
        "backtest_id": job.id, "name": "另一个账户", "cash": 100_000,
    }).json()["id"]
    follows["lineage"] = _Lineage(mine)
    for ident in (mine, other):
        _seed_equity(tmp_path, ident)

    assert '"shadow":true' in client.get(f"/paper/{mine}").text.replace(" ", "")
    assert '"shadow":false' in client.get(f"/paper/{other}").text.replace(" ", "")


def _seed_equity(tmp_path: Path, ident: str) -> None:
    """一个已完成的策略事件 + 当天收盘权益点，曲线才会渲染。"""
    import sqlite3

    with sqlite3.connect(tmp_path / "workbench.sqlite3") as con:
        con.execute(
            "INSERT INTO paper_events(account_id,event_key,scheduled_at,phase,status)"
            " VALUES (?,?,?,?,?)",
            (ident, "2026-09-24:bar:15:00:00", "2026-09-24T15:00:00", "bar", "completed"),
        )
        con.execute(
            "INSERT INTO paper_equity(account_id,at,session,cash,market_value,total_value)"
            " VALUES (?,?,?,?,?,?)",
            (ident, "2026-09-24T15:00:00", "2026-09-24", 100_000.0, 0.0, 100_000.0),
        )


def test_the_run_log_separates_the_pre_gate_stage_from_the_gate(warehouse, tmp_path):
    """没买是价格带拦的还是闸门拦的，页面要能分清（2026-09-24 全被入档前筛掉）。"""
    client, ident = _account_with_run(
        warehouse, tmp_path, candidates=34, routed=0, passed=0,
        reasons={"入档前·不在低开/高开价格带": 31, "入档前·低开·竞价量不足": 3},
    )

    page = client.get(f"/paper/{ident}").text

    assert "候选 34 只 → 入档 0 只 → 闸门通过 0 只，未下单" in page
    assert "入档前·不在低开/高开价格带 31" in page
    assert "入档前·低开·竞价量不足 3" in page
