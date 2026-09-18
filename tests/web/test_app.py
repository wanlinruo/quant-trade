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
    assert "候选 37 只 → 通过 2 只 → 下单 2 笔" in page.text
    assert "高开超过阈值" in page.text and "12" in page.text
    assert "竞价选股" in page.text


def test_run_log_page_says_when_there_is_nothing(client):
    page = client.get(f"/s/{D}/runs")
    assert page.status_code == 200
    assert "当日没有运行记录" in page.text
