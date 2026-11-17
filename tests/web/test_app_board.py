"""新前端打板看板接口 /api/app/board（#662，设计文档 §8.9、§10）。"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from qsys.data.board.store import BoardStore
from qsys.live.messages import MarketSchedule
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.data_views import LiveMarketView
from qsys.web.limit_up_board import LimitUpBoardReader
from qsys.web.views import MonitorService
from tests.web.board_fixtures import DAY, NAMES, PREV, build, q

PREV2 = date(2026, 11, 11)


def tp(hhmmss: str) -> datetime:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return datetime(2026, 11, 12, h, m, s)


def build_prev(tmp_path) -> None:
    """前一交易日也有看板：昨日首板 X、Y，其中 X 今日（11-12）封板 → 1 板晋级率 0.5。"""
    store = BoardStore(tmp_path)
    store.save_preparation(
        PREV, prev_day=PREV2, limits={"X.SH": (11.0, 9.0), "Y.SZ": (11.0, 9.0)},
        prev_heights={"X.SH": 1, "Y.SZ": 1}, heights_error=None, st_symbols=[], concepts=None,
        concept_names={}, concept_error="无概念快照", prepared_at=tp("08:58:00"))
    board = store.load_board_day(PREV)
    quotes = {"X.SH": q("X.SH", "09:30:58", 11.0, bidv=500).model_copy(update={"at": tp("09:30:58")}),
              "Y.SZ": q("Y.SZ", "09:30:58", 10.2).model_copy(update={"at": tp("09:30:58")})}
    store.append_raw(PREV, tp("09:31:00"), quotes)
    events, sentiment = board.ingest(tp("09:31:00"), quotes)
    store.save_step(PREV, tp("09:31:00"), events, sentiment, board.tracked)


class CountingReader(LimitUpBoardReader):
    def __init__(self, root) -> None:
        super().__init__(root, names=lambda s, d: NAMES.get(s, ""), today=lambda: DAY)
        self.calls: list[tuple] = []

    def view(self, day=None, at=None):
        self.calls.append((day, at))
        return super().view(day, at)


class FakeMarket:
    def __init__(self) -> None:
        self.schedule_calls = 0

    def live(self, *, now=None):
        return LiveMarketView(None, True, False)

    def schedule(self):
        self.schedule_calls += 1
        return MarketSchedule(checked_at=datetime(2026, 11, 13, 9, 31), is_trading_day=True, is_open=True)

    def limit_check(self, day=None):
        return None

    def history(self, limit=60):
        return []


@pytest.fixture
def make_client(warehouse):
    def make(board, market=None) -> TestClient:
        cfg = WebConfig()
        return TestClient(create_app(MonitorService(SessionJournal(warehouse), config=cfg), cfg,
                                     limit_up_board=board, market=market))
    return make


def test_app_board_unconfigured(make_client):
    body = make_client(None).get("/api/app/board").json()
    assert body == {"available": False, "message": "打板看板数据源未配置"}


def test_app_board_adds_effect_and_layers(make_client, tmp_path):
    build(tmp_path)
    body = make_client(CountingReader(tmp_path)).get("/api/app/board").json()
    assert body["available"] and body["provisional"] is True and body["mode"] == "live"
    assert body["effect"]["total"] == 2 and body["effect"]["top_level"] == 2
    assert [r["level"] for r in body["layers"]] == [2, 1]
    assert body["layers"][1]["promoted"] == 1 and body["layers"][0]["limit_down"] == 1
    assert body["prev_day"] == PREV.isoformat()
    assert body["schedule"] is None and body["errors"] == {}


def test_effect_null_when_heights_missing(make_client, tmp_path):
    build(tmp_path, heights=False)
    body = make_client(CountingReader(tmp_path)).get("/api/app/board").json()
    assert body["effect"] is None and body["layers"] == []
    assert body["meta"]["heights_error"] == "2026-11-12 limit_list_d 未入库"


def test_latest_view_cached_15s(make_client, tmp_path):
    build(tmp_path)
    r = CountingReader(tmp_path)
    c = make_client(r)
    c.get("/api/app/board")
    c.get("/api/app/board")
    assert [x for x in r.calls if x == (None, None)] == [(None, None)]


def test_replay_is_not_served_from_latest_cache(make_client, tmp_path):
    build(tmp_path)
    c = make_client(CountingReader(tmp_path))
    latest = c.get("/api/app/board").json()
    replay = c.get("/api/app/board", params={"at": "2026-11-13T09:31:15"}).json()
    assert latest["at"] == "2026-11-13T09:31:30"
    assert replay["at"] == "2026-11-13T09:31:15"
    assert replay["sentiment"]["published_at"] == "2026-11-13T09:31:15"


def test_prev_rate_from_previous_day_board(make_client, tmp_path):
    build(tmp_path)
    no_prev = make_client(CountingReader(tmp_path)).get("/api/app/board").json()
    assert all(r["prev_rate"] is None for r in no_prev["layers"])
    build_prev(tmp_path)
    body = make_client(CountingReader(tmp_path)).get("/api/app/board").json()
    rates = {r["level"]: r["prev_rate"] for r in body["layers"]}
    assert rates == {2: None, 1: 0.5}


def test_series_includes_seal_open_reseal_events(make_client, tmp_path):
    build(tmp_path)
    body = make_client(CountingReader(tmp_path)).get(
        "/api/app/board/series", params={"day": "2026-11-13", "symbol": "A.SH"}).json()
    assert body["available"] and len(body["points"]) == 3
    assert [e["kind"] for e in body["events"]] == ["seal", "open", "reseal"]
    assert body["events"][0]["at"] == "2026-11-13T09:30:58"


def test_board_endpoints_no_store(make_client, tmp_path):
    build(tmp_path)
    c = make_client(CountingReader(tmp_path))
    assert c.get("/api/app/board").headers["cache-control"] == "no-store"
    r = c.get("/api/app/board/series", params={"day": "2026-11-13", "symbol": "A.SH"})
    assert r.headers["cache-control"] == "no-store"


def test_schedule_from_shared_live_cache_not_per_request(make_client, tmp_path):
    build(tmp_path)
    m = FakeMarket()
    c = make_client(CountingReader(tmp_path), m)
    for _ in range(3):
        assert c.get("/api/app/board").json()["schedule"]["is_open"] is True
        c.get("/api/app/board", params={"at": "2026-11-13T09:31:15"})
        c.get("/api/app/market")
    assert m.schedule_calls == 1
