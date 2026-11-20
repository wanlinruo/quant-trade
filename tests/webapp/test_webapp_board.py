"""打板看板：新契约、查询层与路由（#662 R2，设计文档 §8.9、§10）。"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from qsys.data.board.store import BoardStore
from qsys.webapp.app import WebappDeps, create_app
from qsys.webapp.config import WebConfig
from qsys.webapp.live_kill_status import LiveKillReader
from qsys.webapp.contracts.board import Board, BoardMeta, BoardSeries, BoardUnavailable
from qsys.webapp.queries.board_reader import BoardView, LimitUpBoardReader
from tests.webapp.board_fixtures import DAY, NAMES, PREV, build, q

PREV2 = date(2026, 11, 11)
OFFSET = "+08:00"


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


@pytest.fixture
def make_client(warehouse):
    def make(board) -> TestClient:
        cfg = WebConfig()
        deps = WebappDeps(live_kill=LiveKillReader(None, None), board=board)
        return TestClient(create_app(deps, cfg))
    return make


# ---- 迁移自 tests/web/test_app_board.py ----

def test_board_unavailable_variants(make_client, tmp_path):
    body = make_client(None).get("/api/app/board")
    assert body.status_code == 200
    assert body.json() == {"schema_version": 1, "available": False,
                           "message": "打板看板数据源未配置", "day": None, "days": []}
    empty = make_client(CountingReader(tmp_path / "none")).get("/api/app/board").json()
    assert empty["available"] is False and empty["message"] == "打板看板采集器尚未产生数据"
    build(tmp_path)
    other = make_client(CountingReader(tmp_path)).get("/api/app/board", params={"day": "2026-11-10"})
    assert other.status_code == 200
    o = BoardUnavailable.model_validate(other.json())
    assert o.day == date(2026, 11, 10) and o.days == [DAY]
    series = make_client(None).get("/api/app/board/series", params={"day": "2026-11-13", "symbol": "A.SH"})
    assert series.status_code == 200
    assert series.json()["available"] is False and series.json()["points"] == []


def test_app_board_adds_effect_and_layers(make_client, tmp_path):
    build(tmp_path)
    body = make_client(CountingReader(tmp_path)).get("/api/app/board").json()
    assert body["available"] and body["provisional"] is True and body["mode"] == "live"
    assert body["effect"]["total"] == 2 and body["effect"]["top_level"] == 2
    assert [r["level"] for r in body["layers"]] == [2, 1]
    assert body["layers"][1]["promoted"] == 1 and body["layers"][0]["limit_down"] == 1
    assert body["meta"]["prev_day"] == PREV.isoformat() and "prev_day" not in body
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
    assert latest["at"] == "2026-11-13T09:31:30+08:00"
    assert replay["at"] == "2026-11-13T09:31:15+08:00"
    assert replay["sentiment"]["published_at"] == "2026-11-13T09:31:15+08:00"


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
    BoardSeries.model_validate(body)
    assert body["available"] and len(body["points"]) == 3
    assert [e["kind"] for e in body["events"]] == ["seal", "open", "reseal"]
    assert body["events"][0]["at"] == "2026-11-13T09:30:58+08:00"
    assert body["points"][0]["at"].endswith(OFFSET)


def test_board_endpoints_no_store(make_client, tmp_path):
    build(tmp_path)
    c = make_client(CountingReader(tmp_path))
    assert c.get("/api/app/board").headers["cache-control"] == "no-store"
    r = c.get("/api/app/board/series", params={"day": "2026-11-13", "symbol": "A.SH"})
    assert r.headers["cache-control"] == "no-store"


# ---- R2 新增 ----

def test_board_times_have_shanghai_offset(make_client, tmp_path):
    build(tmp_path)
    body = make_client(CountingReader(tmp_path)).get("/api/app/board").json()
    assert body["at"].endswith(OFFSET) and body["times"][0].endswith(OFFSET)
    assert body["cards"]["A.SH"]["first_seal"].endswith(OFFSET)
    assert body["timeline"][0]["at"].endswith(OFFSET)
    assert body["meta"]["concept_fetched_at"].endswith(OFFSET)


def test_replay_at_with_offset_matches_local_time(make_client, tmp_path):
    build(tmp_path)
    c = make_client(CountingReader(tmp_path))
    local = c.get("/api/app/board", params={"at": "2026-11-13T09:31:15"}).json()
    utc = c.get("/api/app/board", params={"at": "2026-11-13T01:31:15Z"}).json()
    assert local["sentiment"]["published_at"] == utc["sentiment"]["published_at"] \
        == "2026-11-13T09:31:15+08:00"


def test_series_invalid_params_are_422(make_client, tmp_path):
    build(tmp_path)
    r = CountingReader(tmp_path)
    c = make_client(r)
    for params in ({"day": "2026-11-13", "symbol": "../x"}, {"day": "2026-13-01", "symbol": "A.SH"}):
        bad = c.get("/api/app/board/series", params=params)
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"


def test_contract_covers_reader_fields(tmp_path):
    derived = {"schema_version", "provisional", "effect", "layers", "schedule", "errors"}
    assert set(BoardView.model_fields) - {"status"} == set(Board.model_fields) - derived
    build(tmp_path)
    meta = CountingReader(tmp_path).view().meta
    assert set(meta) - {"concept_sha"} == set(BoardMeta.model_fields)


def test_board_response_validates_against_contract(make_client, tmp_path):
    build(tmp_path)
    body = make_client(CountingReader(tmp_path)).get("/api/app/board").json()
    assert Board.model_validate(body).day == DAY


def test_concept_sha_not_exposed(make_client, tmp_path):
    build(tmp_path)
    text = make_client(CountingReader(tmp_path)).get("/api/app/board").text
    assert "concept_sha" not in text and "s" * 64 not in text


def test_reconciled_diffs_keep_direct_types(make_client, tmp_path):
    """盘后对账差异的直取值原样输出：高度与开板次数为整数，上游缺失为 null（不转成字符串）。"""
    from qsys.data.board.reconcile import Mismatch
    store = build(tmp_path)
    store.save_reconcile(DAY, [Mismatch("A.SH", "height", 2, 3), Mismatch("A.SH", "open_times", 1, None),
                               Mismatch("B.SZ", "first_seal", None, datetime(2026, 11, 13, 9, 31))],
                         datetime(2026, 11, 13, 19, 48))
    r = make_client(CountingReader(tmp_path)).get("/api/app/board")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "final" and body["mismatch_count"] == 3
    assert body["cards"]["A.SH"]["diffs"] == {"height": 3, "open_times": None}
    assert body["cards"]["B.SZ"]["diffs"] == {"first_seal": "2026-11-13 09:31:00"}
