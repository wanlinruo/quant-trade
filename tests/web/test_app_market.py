"""新前端市场总览只读接口 /api/app/market（#662，设计文档 §8.8、§10）。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from qsys.live.messages import MarketOverviewSnapshot, MarketSchedule
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.data_views import LiveMarketView
from qsys.web.views import MonitorService

COLUMNS = ["dt", "limit_up_count", "index_ma20_slope_5d"]


def _row(d: date, slope: float | None) -> dict:
    return {"dt": d, "limit_up_count": 50, "index_ma20_slope_5d": slope}


def _snapshot() -> MarketOverviewSnapshot:
    return MarketOverviewSnapshot(
        session=date(2026, 11, 17), published_at=datetime(2026, 11, 17, 10, 42),
        indices=[], valid_stock_count=5438, advance_count=3000, decline_count=2000,
        flat_count=438, limit_up_count=54, limit_down_count=3, touched_limit_up_count=70,
        broken_board_count=16, broken_board_rate=0.2286, market_advance_ratio=0.623,
        market_median_return_1d=0.0081, market_total_money=1.2e12,
    )


class FakeMarket:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows if rows is not None else [_row(date(2026, 11, 17), 0.1)]
        self.calls = {"live": 0, "schedule": 0, "limit_check": 0, "history": 0}
        self.live_error: Exception | None = None

    def live(self, *, now=None) -> LiveMarketView:
        self.calls["live"] += 1
        if self.live_error:
            raise self.live_error
        return LiveMarketView(_snapshot(), False, True)

    def schedule(self) -> MarketSchedule:
        self.calls["schedule"] += 1
        return MarketSchedule(checked_at=datetime(2026, 11, 17, 10, 42), is_trading_day=True, is_open=True)

    def limit_check(self, day=None) -> dict:
        self.calls["limit_check"] += 1
        return {"checked": 5441, "mismatch_count": 0}

    def history(self, limit: int = 60) -> list[dict]:
        self.calls["history"] += 1
        return sorted(self.rows, key=lambda r: r["dt"], reverse=True)[:limit]


@pytest.fixture
def make_client(warehouse):
    def make(market) -> TestClient:
        cfg = WebConfig()
        return TestClient(create_app(MonitorService(SessionJournal(warehouse), config=cfg), cfg, market=market))
    return make


def test_app_market_disabled(make_client) -> None:
    r = make_client(None).get("/api/app/market")
    assert r.status_code == 200
    assert r.json() == {"enabled": False}


def test_regime_rows_keep_nulls(make_client) -> None:
    rows = [_row(date(2026, 11, 13), 0.1), _row(date(2026, 11, 14), None), _row(date(2026, 11, 17), 0.2)]
    body = make_client(FakeMarket(rows)).get("/api/app/market").json()
    assert len(body["regime"]) == 3
    day = body["regime"][1]
    assert (day["dt"], day["limit_up_count"], day["index_ma20_slope_5d"]) == ("2026-11-14", 50, None)
    assert "index_ma20_slope_5d" in body["regime_columns"]


def test_regime_includes_every_schema_column(make_client) -> None:
    body = make_client(FakeMarket([{"dt": date(2026, 11, 17)}])).get("/api/app/market").json()
    row = body["regime"][0]
    assert set(row) == set(body["regime_columns"])
    assert row["index_ma20_slope_5d"] is None and row["limit_up_count"] is None


def test_regime_sorted_ascending_and_capped_at_60(make_client) -> None:
    start = date(2026, 1, 1)
    rows = [_row(start + timedelta(days=i), float(i)) for i in range(80)]
    body = make_client(FakeMarket(rows)).get("/api/app/market").json()
    dts = [r["dt"] for r in body["regime"]]
    assert len(dts) == 60
    assert dts == sorted(dts)
    assert dts[-1] == (start + timedelta(days=79)).isoformat()


def test_live_parts_are_cached_for_15s(make_client) -> None:
    fake = FakeMarket()
    c = make_client(fake)
    a = c.get("/api/app/market").json()
    c.get("/api/app/market")
    assert fake.calls == {"live": 1, "schedule": 1, "limit_check": 1, "history": 1}
    assert a["live"]["available"] is True and a["live"]["stale"] is False
    assert a["live"]["snapshot"]["limit_up_count"] == 54
    assert a["schedule"]["is_open"] is True
    assert a["limit_check"] == {"checked": 5441, "mismatch_count": 0}


def test_part_failure_is_isolated_and_not_leaked(make_client) -> None:
    fake = FakeMarket()
    fake.live_error = RuntimeError("/secret/path")
    r = make_client(fake).get("/api/app/market")
    body = r.json()
    assert body["live"] is None
    assert body["errors"] == {"live": "读取失败"}
    assert body["schedule"]["is_open"] is True
    assert len(body["regime"]) == 1
    assert "secret" not in r.text


def test_missing_reasons_cite_issue(make_client) -> None:
    body = make_client(FakeMarket()).get("/api/app/market").json()
    assert set(body["missing"]) == {"index_intraday", "return_distribution"}
    assert all("#675" in v for v in body["missing"].values())


def test_endpoint_no_store(make_client) -> None:
    r = make_client(FakeMarket()).get("/api/app/market")
    assert r.headers["cache-control"] == "no-store"
    json.loads(r.text)
