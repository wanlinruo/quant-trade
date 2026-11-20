from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from qsys.webapp.contracts.market import MarketOverview
from qsys.webapp.contracts.trading import LiveTradingOverview, TradingOrders, TradingRuns
from qsys.webapp.queries.today import TodayQuery
from qsys.webapp.routes.today import today_router

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 19, 10, 0, tzinfo=TZ)
DAY = date(2026, 11, 19)


class Trading:
    def live(self, day):
        return LiveTradingOverview.model_validate({
            "day": {"selected": str(DAY), "available": [str(DAY)]}, "posture": "trading", "posture_label": "可下单",
            "observation": {"at": NOW.isoformat(), "cash": 90000, "market_value": 10000, "total_value": 100000, "return_ratio": None},
            "positions": [], "heartbeats": [], "kill": {"state": "clear", "label": "已解除", "message": "", "sources": []},
            "risks": [], "equity": [], "order_count": 0, "missing": {"return_ratio": "缺流水"}})

    def orders(self, day):
        return TradingOrders(day={"selected": DAY, "available": [DAY]}, orders=[], fills=[], rejects=[], missing={})

    def runs(self, day):
        return TradingRuns(day={"selected": DAY, "available": [DAY]}, runs=[{
            "event_key": "auction", "phase": "auction", "scheduled_at": "2026-11-19T09:25:00+08:00",
            "started_at": "2026-11-19T09:25:01+08:00", "finished_at": "2026-11-19T09:25:02+08:00",
            "duration_ms": 1000, "status": "completed", "candidates": 1, "routed": 0, "passed": 0,
            "orders": 0, "rejects": 0, "summary": "未下单", "reasons": {}, "note": "", "error": "",
            "event_status": "completed", "event_detail": ""}], report_text=None, missing={})


class Market:
    def overview(self):
        return MarketOverview.model_validate({"live": {"available": True, "stale": False, "snapshot": {
            "session": str(DAY), "published_at": NOW.isoformat(), "indices": [{"symbol": "000001.SH", "name": "上证指数", "price": 3400, "change_pct": 1, "amount": 1}],
            "valid_stock_count": 1, "advance_count": 1, "decline_count": 0, "flat_count": 0,
            "limit_up_count": 10, "limit_down_count": 2, "touched_limit_up_count": 12, "broken_board_count": 2,
            "broken_board_rate": 1 / 6, "market_advance_ratio": 1, "market_median_return_1d": .01,
            "market_total_money": 1, "limit_price_missing_count": 0, "limit_up_unknown_count": 0,
            "limit_down_unknown_count": 0, "coverage": None}},
            "schedule": {"checked_at": NOW.isoformat(), "is_trading_day": True, "is_open": True, "next_open_at": None},
            "limit_check": None, "regime": [], "missing": {}, "errors": {}})


class Health:
    def link_samples(self, since):
        return [{"sampled_at_utc": NOW, "source": "agent", "age_s": 1.0, "threshold_s": 10.0, "fresh": True}]


def test_today_combines_direct_facts_without_inventing_intraday():
    got = TodayQuery(trading=Trading(), market=Market(), health=Health(), clock=lambda: NOW).overview(DAY)
    assert got.kpis.account_total == 100000
    assert got.kpis.index_change_ratio == .01
    assert got.kpis.seal_ratio == 5 / 6
    assert got.state == "running"
    assert got.events[0].state == "completed"
    assert "#675" in got.missing["index_intraday"]
    assert got.links[0].points[0].fresh is True


def test_today_keeps_independent_sources_available_when_one_fails():
    class BadMarket:
        def overview(self): raise RuntimeError("secret path")
    got = TodayQuery(trading=Trading(), market=BadMarket(), health=Health(), clock=lambda: NOW).overview(DAY)
    assert got.kpis.account_total == 100000
    assert got.kpis.index_price is None
    assert got.missing["market"] == "市场状态不可读或未配置"
    assert "secret" not in str(got.model_dump())


def test_today_route_disabled_without_sources():
    app = FastAPI(); app.include_router(today_router(None))
    response = TestClient(app).get("/api/app/today")
    assert response.json() == {"schema_version": 1, "enabled": False}
    assert response.headers["cache-control"] == "no-store"
