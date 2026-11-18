"""市场总览：新契约与查询层（#662，设计文档 §8.8、§10）。"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import polars as pl
import pytest
from fastapi.testclient import TestClient

from qsys.data import schema
from qsys.live.messages import MarketOverviewSnapshot, MarketSchedule
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.live_kill_status import LiveKillReader
from qsys.web.views import MonitorService
from qsys.webapp.app import WebappDeps
from qsys.webapp.contracts.market import MarketOverview
from qsys.webapp.queries.market import MISSING, MarketQuery

NOW = datetime(2026, 11, 17, 10, 42, 20)


def _snapshot(**over) -> MarketOverviewSnapshot:
    base = dict(
        session=date(2026, 11, 17), published_at=datetime(2026, 11, 17, 10, 42),
        indices=[], valid_stock_count=5438, advance_count=3000, decline_count=2000,
        flat_count=438, limit_up_count=54, limit_down_count=3, touched_limit_up_count=70,
        broken_board_count=16, broken_board_rate=0.2286, market_advance_ratio=0.623,
        market_median_return_1d=0.0081, market_total_money=1.2e12)
    return MarketOverviewSnapshot(**{**base, **over})


REPORT = {"session": "2026-11-17", "checked_at": "2026-11-17T09:00:05", "checked": 5441,
          "missing_in_qmt": 2, "mismatched": 1, "unbounded_equivalent": 3,
          "samples": [{"symbol": "600000.SH", "tushare": [11.11, 9.09], "qmt": [11.12, 9.09]}],
          "missing_samples": [{"symbol": "600001.SH", "tushare": [11.0, 9.0], "qmt": None},
                              {"symbol": "200001.SH", "tushare": [5.0, 4.0], "qmt": None}]}


class FakeTransport:
    def __init__(self) -> None:
        self.keys = {
            "state:market_overview": _snapshot().model_dump_json(),
            "state:market_schedule": MarketSchedule(checked_at=NOW, is_trading_day=True, is_open=True).model_dump_json(),
            "state:price_limits_check:20261117": json.dumps(REPORT),
        }
        self.calls = 0
        self.error: Exception | None = None
        self._lock = threading.Lock()

    def get_key(self, key: str):
        with self._lock:
            self.calls += 1
        if self.error:
            raise self.error
        return self.keys.get(key)


def regime_frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=pl.from_arrow(schema.MARKET_REGIME.empty_table()).schema)


def row(d: date, slope: float | None = 0.1) -> dict:
    return {"dt": d, "limit_up_count": 50, "index_ma20_slope_5d": slope}


def make(transport=None, rows=None, roster=frozenset({"600001.SH"}), clock=None) -> MarketQuery:
    frame = regime_frame(rows if rows is not None else [row(date(2026, 11, 17))])
    return MarketQuery(transport if transport is not None else FakeTransport(),
                       regime_source=lambda: frame, roster_source=lambda: roster,
                       now=lambda: NOW, clock=clock or (lambda: 0.0))


def test_overview_shape_and_snapshot_time_has_offset():
    body = make().overview().model_dump(mode="json")
    assert body["enabled"] is True and body["schema_version"] == 1
    snap = body["live"]["snapshot"]
    assert snap["limit_up_count"] == 54 and snap["published_at"] == "2026-11-17T10:42:00+08:00"
    assert body["live"]["stale"] is False and body["schedule"]["is_open"] is True
    MarketOverview.model_validate(body)


def test_stale_computed_at_read_time():
    q = MarketQuery(FakeTransport(), regime_source=lambda: regime_frame([]), roster_source=frozenset,
                    now=lambda: NOW + timedelta(seconds=60), clock=lambda: 0.0)
    assert q.overview().live.stale is True


def test_limit_check_pairs_and_roster_split():
    lc = make().overview().limit_check
    assert lc.samples[0].tushare.up == 11.11 and lc.samples[0].qmt.up == 11.12
    assert lc.missing_samples[0].qmt is None
    assert lc.missing_in_roster == ["600001.SH"] and lc.missing_outside_roster == ["200001.SH"]
    assert lc.missing_split_complete is True


def test_limit_check_roster_split_only_when_roster_available():
    for roster in (frozenset(), None):
        q = MarketQuery(FakeTransport(), regime_source=lambda: regime_frame([]),
                        roster_source=(lambda r=roster: r), now=lambda: NOW, clock=lambda: 0.0)
        lc = q.overview().limit_check
        assert lc.missing_in_roster is None and lc.missing_split_complete is None


def test_regime_rows_keep_nulls_and_all_columns():
    rows = [row(date(2026, 11, 13)), row(date(2026, 11, 14), None), row(date(2026, 11, 17), 0.2)]
    body = make(rows=rows).overview().model_dump(mode="json")
    assert len(body["regime"]) == 3
    day = body["regime"][1]
    assert day["dt"] == "2026-11-14" and day["index_ma20_slope_5d"] is None
    assert set(day) == set(schema.MARKET_REGIME.names)


def test_regime_sorted_ascending_and_capped_at_60():
    start = date(2026, 1, 1)
    body = make(rows=[row(start + timedelta(days=i), float(i)) for i in range(80)]).overview()
    dts = [r.dt for r in body.regime]
    assert len(dts) == 60 and dts == sorted(dts) and dts[-1] == start + timedelta(days=79)


def test_live_parts_cached_15s_single_flight():
    t, now = FakeTransport(), [0.0]
    q = make(transport=t, clock=lambda: now[0])
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda _: q.overview(), range(16)))
    assert t.calls == 3
    now[0] = 15.01
    q.overview()
    assert t.calls == 6


def test_part_failure_is_isolated_and_not_leaked():
    t = FakeTransport()
    t.error = RuntimeError("/secret/path")
    body = make(transport=t).overview()
    assert body.live is None and body.schedule is None and body.limit_check is None
    assert body.errors == {"live": "读取失败", "schedule": "读取失败", "limit_check": "读取失败"}
    assert len(body.regime) == 1
    assert "secret" not in body.model_dump_json()


def test_missing_reasons_cite_issue():
    assert set(MISSING) == {"index_intraday", "return_distribution"}
    assert all("#675" in v for v in MISSING.values())


@pytest.fixture
def client(warehouse):
    def build(market) -> TestClient:
        cfg = WebConfig()
        deps = WebappDeps(live_kill=LiveKillReader(None, None), market=market)
        return TestClient(create_app(MonitorService(SessionJournal(warehouse), config=cfg), cfg, webapp=deps))
    return build


def test_route_disabled_and_no_store(client):
    r = client(None).get("/api/app/market")
    assert r.status_code == 200 and r.json() == {"schema_version": 1, "enabled": False}
    r = client(make()).get("/api/app/market")
    assert r.headers["cache-control"] == "no-store" and r.json()["enabled"] is True


def test_board_schedule_uses_market_query_cache(warehouse, tmp_path):
    from tests.webapp.board_fixtures import build, reader
    t = FakeTransport()
    build(tmp_path)
    cfg = WebConfig()
    deps = WebappDeps(live_kill=LiveKillReader(None, None), market=make(transport=t))
    c = TestClient(create_app(MonitorService(SessionJournal(warehouse), config=cfg), cfg,
                              limit_up_board=reader(tmp_path), webapp=deps))
    for _ in range(3):
        assert c.get("/api/app/board").json()["schedule"]["is_open"] is True
        c.get("/api/app/market")
    assert t.calls == 3
