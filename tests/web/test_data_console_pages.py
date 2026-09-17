"""数据台页面：还没跑过交易时也必须打得开。"""

from datetime import date

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from qsys.data import schema
from qsys.data.storage import Warehouse
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.data_views import DataService, MarketService
from qsys.web.views import MonitorService

D = [date(2024, 1, d) for d in (2, 3, 4)]


def seed(warehouse: Warehouse) -> None:
    n = len(D)
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": ["600001.SH"] * n, "dt": list(D),
                "open": [10.0] * n, "high": [10.0] * n, "low": [10.0] * n,
                "close": [10.0] * n, "volume": [100] * n, "amount": [0.0] * n,
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )
    warehouse.write(
        "market_regime",
        pa.table(
            {
                "dt": list(D), "limit_up_count": [10, 20, 30],
                "limit_down_count": [1] * n, "limit_up_down_ratio": [1.0] * n,
                "touched_limit_up_count": [15, 25, 35], "broken_board_count": [5] * n,
                "broken_board_rate": [0.3] * n, "first_board_count": [7] * n,
                "market_advance_ratio": [0.5] * n,
                "market_median_return_1d": [0.01] * n,
                "market_total_money": [1e10] * n,
                "prior_limitup_nextday_return_mean": [0.02] * n,
                "prior_limitup_nextday_return_median": [0.02] * n,
                "valid_stock_count": [100] * n,
                "index_ma20_slope_5d": [0.001, 0.002, None],
            },
            schema=schema.MARKET_REGIME,
        ),
        year=2024,
    )


def client_for(warehouse):
    config = WebConfig()
    return TestClient(
        create_app(
            MonitorService(SessionJournal(warehouse), config=config),
            config,
            DataService(warehouse),
            market=MarketService(warehouse),
        )
    )


def test_data_overview_opens_without_any_trading_session(warehouse):
    """监控台读 live_* 表，数据台读数据层——没跑过交易也该有东西看。"""
    seed(warehouse)
    response = client_for(warehouse).get("/data")
    assert response.status_code == 200
    assert "bars_daily" in response.text
    assert "market_regime" in response.text


def test_data_overview_opens_on_an_empty_warehouse(warehouse):
    """空仓库也不能报错——出事的时候页面必须打得开。"""
    assert client_for(warehouse).get("/data").status_code == 200


def test_regime_page_renders_a_chart(warehouse):
    seed(warehouse)
    response = client_for(warehouse).get("/market")
    assert response.status_code == 200
    assert "<svg" in response.text


def test_regime_page_shows_a_dash_for_missing_index_slope(warehouse):
    """指数因子为 null 的日子显示 —— 而不是 0 或 None。"""
    seed(warehouse)
    text = client_for(warehouse).get("/market").text
    assert "None" not in text


def test_old_selection_page_redirects_to_backtests(warehouse):
    seed(warehouse)
    response = client_for(warehouse).get("/data/selection", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/backtests"


def test_old_regime_page_redirects_to_market(warehouse):
    response = client_for(warehouse).get("/data/regime", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/market"


def test_the_data_console_is_absent_when_not_wired(warehouse):
    config = WebConfig()
    client = TestClient(
        create_app(MonitorService(SessionJournal(warehouse), config=config), config)
    )
    assert client.get("/data").status_code == 404


def test_the_root_points_at_market(warehouse):
    seed(warehouse)
    response = client_for(warehouse).get("/", follow_redirects=False)
    assert response.headers["location"] == "/market"


def test_data_overview_lists_the_delisting_pool(warehouse):
    from qsys.data.pipeline.delisting import build_delisting_pool

    warehouse.write("name_history", pa.table({
        "symbol": ["300344.SZ"], "name": ["立方退"], "start_date": [date(2024, 1, 3)],
        "end_date": [None], "ann_date": [date(2024, 1, 2)], "change_reason": ["退市整理期"],
    }, schema=schema.NAME_HISTORY))
    build_delisting_pool(warehouse, D[0], D[-1], extra_sessions=D)
    page = client_for(warehouse).get("/data")
    assert "立方退" in page.text and "2024-01-04" in page.text
