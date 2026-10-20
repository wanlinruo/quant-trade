from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from qsys.live.messages import MarketIndexQuote, MarketOverviewSnapshot, MarketSchedule
from qsys.live.transport import MemoryTransport
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.data_views import DataService, MarketService
from qsys.web.views import MonitorService


def _snapshot(at):
    return MarketOverviewSnapshot(
        session=at.date(), published_at=at,
        indices=[MarketIndexQuote(symbol="000001.SH", name="上证指数",
                                  price=3300, change_pct=.012)],
        valid_stock_count=5000, advance_count=3000, decline_count=1900,
        flat_count=100, limit_up_count=55, limit_down_count=3,
        touched_limit_up_count=80, broken_board_count=25,
        broken_board_rate=.3125, market_advance_ratio=.6122,
        market_median_return_1d=.004, market_total_money=800_000_000_000,
    )


def _client(warehouse, transport, *, now=None):
    cfg = WebConfig()
    market = MarketService(warehouse, transport)
    app = create_app(MonitorService(SessionJournal(warehouse), config=cfg), cfg,
                     DataService(warehouse), market=market)
    return TestClient(app), market


def test_market_page_and_json_render_live_snapshot(warehouse):
    transport = MemoryTransport()
    snapshot = _snapshot(datetime.now())
    transport.set_key("state:market_overview", snapshot.model_dump_json())
    transport.set_key("state:market_schedule", MarketSchedule(
        checked_at=datetime.now(), is_trading_day=True, is_open=True,
    ).model_dump_json())
    client, _ = _client(warehouse, transport)
    assert client.get("/", follow_redirects=False).headers["location"] == "/market"
    body = client.get("/market").text
    assert "上证指数" in body
    assert "3,000" not in body  # 家数保持紧凑数字
    payload = client.get("/api/market/overview").json()
    assert payload["available"] is True
    assert payload["stale"] is False
    assert payload["market_open"] is True
    assert payload["snapshot"]["limit_up_count"] == 55


def test_market_service_marks_old_snapshot_stale(warehouse):
    transport = MemoryTransport()
    now = datetime(2026, 9, 16, 10, 0)
    transport.set_key("state:market_overview", _snapshot(now - timedelta(seconds=46)).model_dump_json())
    _, market = _client(warehouse, transport)
    view = market.live(now=now)
    assert view.available is True
    assert view.stale is True


def test_market_console_survives_a_bad_or_missing_snapshot(warehouse):
    transport = MemoryTransport()
    transport.set_key("state:market_overview", "not-json")
    client, market = _client(warehouse, transport)
    assert market.live().available is False
    response = client.get("/market")
    assert response.status_code == 200
    assert "无实时快照" in response.text


def test_market_page_uses_scheduled_timeout_instead_of_constant_interval(warehouse):
    transport = MemoryTransport()
    transport.set_key("state:market_schedule", MarketSchedule(
        checked_at=datetime(2026, 9, 16, 12), is_trading_day=True,
        is_open=False, next_open_at=datetime(2026, 9, 16, 13),
    ).model_dump_json())
    client, market = _client(warehouse, transport)
    body = client.get("/market").text
    assert "setInterval" not in body
    assert "next_open_at" in body
    assert market.schedule().next_open_at == datetime(2026, 9, 16, 13)


def test_market_page_shows_the_qmt_limit_check_result(warehouse):
    """D-19：Tushare stk_limit 与 QMT 核对不一致必须在页面上看得见，不能只躺在 Redis 里。"""
    import json

    transport = MemoryTransport()
    today = datetime.now().date()
    transport.set_key(f"state:price_limits_check:{today:%Y%m%d}", json.dumps({
        "session": str(today), "checked": 5218, "missing_in_qmt": 2, "mismatched": 1,
        "samples": [{"symbol": "000010.SZ", "tushare": [1.73, 1.41], "qmt": [1.74, 1.41]}],
    }))
    client, market = _client(warehouse, transport)
    check = market.limit_check()
    assert check["mismatched"] == 1
    body = client.get("/market").text
    assert "涨跌停价核对" in body and "000010.SZ" in body


def test_market_page_separates_real_mismatch_unbounded_and_coverage(warehouse):
    """#283：只有真实价格不一致标红；无限制等价与 QMT 覆盖缺口分开展示。"""
    import json

    transport = MemoryTransport()
    today = datetime.now().date()
    transport.set_key(f"state:price_limits_check:{today:%Y%m%d}", json.dumps({
        "session": str(today), "checked": 5653, "missing_in_qmt": 41, "mismatched": 0,
        "unbounded_equivalent": 2, "samples": [],
        "missing_samples": [{"symbol": "430017.BJ", "tushare": [12.0, 8.0], "qmt": None}],
    }))
    client, _ = _client(warehouse, transport)
    body = client.get("/market").text
    assert '<p class="down">' not in body.split("涨跌停价核对")[1].split("</section>")[0]
    assert "价格不一致 0 只" in body
    assert "无涨跌幅限制" in body and "2 只" in body
    assert "QMT 未覆盖 41 只" in body and "430017.BJ" in body


def test_market_page_says_when_the_limit_check_has_not_run(warehouse):
    client, market = _client(warehouse, MemoryTransport())
    assert market.limit_check() is None
    assert "未核对" in client.get("/market").text
