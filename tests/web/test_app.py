from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient

from qsys.core.events import Reject
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol
from qsys.live.messages import AccountSnapshot, PositionSnapshot
from qsys.live.store import LiveStore
from qsys.live.transport import MemoryTransport
from qsys.ops.journal import SessionJournal
from qsys.ops.strategy_log import RunReport, build_record
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService

CMB = Symbol("600036", Market.SH)
D0 = date(2024, 1, 2)
D = date(2024, 1, 3)
T = datetime(2024, 1, 3, 9, 30)


@pytest.fixture
def client(warehouse) -> TestClient:
    journal = SessionJournal(warehouse)
    journal.record_equity(D0, cash=100_000.0, market_value=0.0)
    journal.record_equity(D, cash=88_000.0, market_value=13_000.0)
    journal.record_fills(D, [Fill("a", CMB, Side.BUY, 1000, 12.0, 5.1, T)])
    journal.record_rejects(D, [Reject("b", CMB, Side.BUY, 100, "涨停 <买不到>", T)])
    journal.record_reconciliation(D, "intraday", is_consistent=True, at=T)

    transport = MemoryTransport()
    transport.set_key(
        "state:account",
        AccountSnapshot(at=T, cash=88_000.0, positions={
            "600036.SH": PositionSnapshot(total_qty=1000, available_qty=0, avg_cost=12.01)
        }).model_dump_json(),
    )

    cfg = WebConfig()
    service = MonitorService(journal, config=cfg, transport=transport,
                             quality_summary_provider=lambda: "quality: clean")
    return TestClient(create_app(service, cfg))


def test_healthz_is_always_available(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root_redirects_to_live_when_market_console_is_not_wired(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/live"


def test_overview_page_renders(client):
    r = client.get(f"/s/{D}")
    assert r.status_code == 200
    assert "600036.SH" in r.text
    assert "<svg" in r.text


def test_overview_shows_the_equity_value(client):
    assert "101,000.00" in client.get(f"/s/{D}").text


def test_trades_page_lists_fills_and_rejects(client):
    r = client.get(f"/s/{D}/trades")
    assert r.status_code == 200
    assert "600036.SH" in r.text


def test_reject_reason_is_html_escaped(client):
    """拒单原因可能含 HTML 字符，必须转义。"""
    r = client.get(f"/s/{D}/trades")
    assert "&lt;买不到&gt;" in r.text
    assert "<买不到>" not in r.text


def test_health_page_shows_reconciliation_and_quality(client):
    r = client.get(f"/s/{D}/health")
    assert r.status_code == 200
    assert "intraday" in r.text
    assert "quality: clean" in r.text


def test_report_page_renders(client):
    r = client.get(f"/s/{D}/report")
    assert r.status_code == 200
    assert "每日运行报告" in r.text


def test_report_text_endpoint_is_plain_text(client):
    r = client.get(f"/s/{D}/report.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "每日运行报告" in r.text


def test_status_fragment_is_small_and_pollable(client):
    r = client.get(f"/fragment/{D}/status")
    assert r.status_code == 200
    assert "<html" not in r.text.lower()


def test_unknown_session_returns_404(client):
    assert client.get("/s/2020-01-01/trades").status_code == 404


def test_malformed_session_returns_422(client):
    assert client.get("/s/not-a-date").status_code == 422


def test_pages_have_no_external_resources(client):
    """零外部依赖：页面不得引用任何 CDN。"""
    for path in (f"/s/{D}", f"/s/{D}/trades", f"/s/{D}/health", f"/s/{D}/report"):
        text = client.get(path).text
        assert "cdn." not in text
        assert "//unpkg" not in text
        assert "googleapis" not in text


def test_no_write_endpoints_exist(client):
    """一期 Web 只读——不得存在任何写接口。"""
    methods = {m for route in client.app.routes for m in getattr(route, "methods", set())}
    assert methods <= {"GET", "HEAD", "OPTIONS"}


def test_empty_journal_root_shows_a_placeholder(warehouse):
    cfg = WebConfig()
    c = TestClient(create_app(MonitorService(SessionJournal(warehouse), config=cfg), cfg))
    r = c.get("/")
    assert r.status_code == 200
    assert "尚未产生" in r.text
    assert "行情网关" in r.text


def test_run_log_page_lists_events_and_reasons(warehouse, tmp_path):
    journal = SessionJournal(warehouse)
    journal.record_equity(D, cash=50_000.0, market_value=0.0)
    store = LiveStore(tmp_path / "live.sqlite3")
    run_report = RunReport()
    run_report.report(candidates=37, passed=2, reasons={"高开超过阈值": 12})
    store.record_run(
        build_record(
            run_report,
            event_key=f"{D}:auction:09:30:00",
            session=D,
            phase="auction",
            started_at=datetime.combine(D, time(9, 30)),
            finished_at=datetime.combine(D, time(9, 30, 1)),
            cash=50_000.0,
            market_value=0.0,
            position_count=0,
            orders=2,
            rejects=0,
        )
    )
    cfg = WebConfig()
    web = TestClient(
        create_app(MonitorService(journal, config=cfg, store=store), cfg)
    )

    page = web.get(f"/s/{D}/runs")

    assert page.status_code == 200
    assert "运行日志" in page.text
    assert "候选 37 只 → 闸门通过 2 只 → 下单 2 笔" in page.text
    assert "高开超过阈值" in page.text and "12" in page.text
    assert "竞价选股" in page.text


def test_run_log_page_says_when_there_is_nothing(client):
    page = client.get(f"/s/{D}/runs")
    assert page.status_code == 200
    assert "当日没有运行记录" in page.text


class _Lineage:
    paper_account_id = "acct-1"
    paper_account_name = "模拟-09:25入场"
    paper_status = "running"
    backtest_id = "bt-9"
    strategy_name = "structural_v1_2"
    version_id = "v1"
    params = {"auction_at": "09:25:10", "entry_price_buffer": 0.03}


class _PaperStub:
    """只提供影子盘页要用的两个查询。"""

    class _Account:
        initial_cash = 100_000.0

    def account(self, ident):
        return self._Account() if ident == "acct-1" else None

    def equity_daily(self, ident):
        return [
            {"session": "2024-01-02", "total_value": 100_000.0},
            {"session": "2024-01-03", "total_value": 102_000.0},
        ]


@pytest.mark.parametrize("closing_equity", [50_500.0, 100_022.72])
def test_live_page_does_not_infer_returns_without_cash_flow_records(warehouse, closing_equity):
    journal = SessionJournal(warehouse)
    journal.record_equity(D0, cash=50_000.0, market_value=0.0)      # 2024-01-02
    journal.record_equity(D, cash=closing_equity, market_value=0.0)
    cfg = WebConfig()
    service = MonitorService(journal, config=cfg)
    http = TestClient(create_app(service, cfg, paper=_PaperStub(),
                                 shadow_lineage=lambda: _Lineage()))

    body = http.get("/live").text

    assert "策略与来源" in body and "模拟-09:25入场" in body
    assert "bt-9" in body and "竞价决策时刻" in body and "09:25:10" in body
    assert "影子盘 vs 模拟交易" in body
    assert "缺少完整资金变动流水" in body
    assert "收益对比暂不可用" in body
    comparison = body.split("影子盘 vs 模拟交易", 1)[1].split("</div>", 1)[0]
    assert "+100.05%" not in comparison and "+1.00%" not in comparison
    assert "按累计收益率比较" not in body
    assert f"{closing_equity:,.2f}" in body


def test_health_page_survives_a_null_cash_diff(warehouse):
    """现金等价物缺市值时 `cash_diff` 是 null（#134）。模板直接 format 会抛
    TypeError，让 /health 变成 500——而那正是对账判不了时运维第一个打开的页面。"""
    journal = SessionJournal(warehouse)
    journal.record_equity(D, cash=100_000.0, market_value=0.0)
    journal.record_reconciliation(
        D, "intraday", mode="shadow", is_consistent=False, cash_diff=None,
        n_mismatches=0, detail="1 个现金等价物缺市值", at=T,
    )
    cfg = WebConfig()
    service = MonitorService(journal, config=cfg, transport=MemoryTransport(),
                             quality_summary_provider=lambda: "quality: clean")
    client = TestClient(create_app(service, cfg))

    r = client.get(f"/s/{D}/health")

    assert r.status_code == 200
    assert "无法判定" in r.text
