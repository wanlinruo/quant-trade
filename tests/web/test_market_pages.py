from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from qsys.live.messages import MarketIndexQuote, MarketOverviewSnapshot
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
    client, _ = _client(warehouse, transport)
    assert client.get("/", follow_redirects=False).headers["location"] == "/market"
    body = client.get("/market").text
    assert "上证指数" in body
    assert "3,000" not in body  # 家数保持紧凑数字
    payload = client.get("/api/market/overview").json()
    assert payload["available"] is True
    assert payload["stale"] is False
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
