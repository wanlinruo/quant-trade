import sqlite3
from datetime import date, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from qsys.data.board.concepts import ConceptSnapshot
from qsys.data.board.reconcile import Mismatch
from qsys.data.board.store import BoardStore
from qsys.live.messages import MarketQuote
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.limit_up_board import LimitUpBoardReader
from qsys.web.views import MonitorService

DAY, PREV = date(2026, 11, 13), date(2026, 11, 12)
NAMES = {"A.SH": "甲科技", "B.SZ": "乙智能", "C.SZ": "丙精密"}


def t(hhmmss):
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return datetime(2026, 11, 13, h, m, s)


def q(symbol, hhmmss, price, *, high=None, bidv=0, askv=0, volume=0):
    return MarketQuote(symbol=symbol, at=t(hhmmss), price=price, open=10.0, high=high or price,
                       low=9.0, prev_close=10.0, volume=volume, bid1=price, bid1_volume=bidv,
                       ask1_volume=askv)


def build(tmp_path, *, concepts=True, heights=True):
    store = BoardStore(tmp_path)
    snap = ConceptSnapshot(DAY, t("08:40:00"),
                           {"885001.TI": frozenset({"A.SH", "B.SZ"}),
                            "885002.TI": frozenset({"C.SZ"})},
                           {"885001.TI": 2, "885002.TI": 1}, "s" * 64) if concepts else None
    store.save_preparation(
        DAY, prev_day=PREV,
        limits={s: (11.0, 9.0) for s in ("A.SH", "B.SZ", "C.SZ")},
        prev_heights={"A.SH": 1, "B.SZ": 0, "C.SZ": 2} if heights else None,
        heights_error=None if heights else "2026-11-12 limit_list_d 未入库",
        st_symbols=[], concepts=snap,
        concept_names={"885001.TI": "机器人", "885002.TI": "固态电池"},
        concept_error=None if concepts else "RuntimeError: 积分不足",
        prepared_at=t("08:58:00"))
    board = store.load_board_day(DAY)
    steps = [
        ("09:31:00", {"A.SH": q("A.SH", "09:30:58", 11.0, bidv=800, volume=100),
                      "B.SZ": q("B.SZ", "09:30:58", 10.75, volume=50),
                      "C.SZ": q("C.SZ", "09:30:58", 10.3)}),
        ("09:31:15", {"A.SH": q("A.SH", "09:31:13", 10.9, high=11.0, askv=3, volume=180),
                      "B.SZ": q("B.SZ", "09:31:13", 11.0, bidv=5, volume=90),
                      "C.SZ": q("C.SZ", "09:31:13", 10.1)}),
        ("09:31:30", {"A.SH": q("A.SH", "09:31:28", 11.0, bidv=60, volume=200),
                      "B.SZ": q("B.SZ", "09:31:28", 11.0, bidv=5, volume=95),
                      "C.SZ": q("C.SZ", "09:31:28", 9.0)}),
    ]
    for hhmmss, quotes in steps:
        store.append_raw(DAY, t(hhmmss), quotes)
        events, sentiment = board.ingest(t(hhmmss), quotes)
        store.save_step(DAY, t(hhmmss), events, sentiment, board.tracked)
    return store


def shadow():
    snap = SimpleNamespace(model_version="v-test", rolling_precision_at_20=0.3,
                           limit_up_scores=[SimpleNamespace(symbol="A.SH", p_close_limit=0.42)])
    calls = []

    def view(prediction_date):
        calls.append(prediction_date)
        return SimpleNamespace(state="ready", message="ok", snapshot=snap)
    return SimpleNamespace(view=view, calls=calls)


def reader(tmp_path, today=DAY, sh=None):
    return LimitUpBoardReader(tmp_path, names=lambda s, d: NAMES.get(s, ""), shadow=sh,
                              today=lambda: today)


def test_latest_view_builds_ladder_columns_and_compare(tmp_path):
    build(tmp_path)
    sh = shadow()
    v = reader(tmp_path, sh=sh).view()
    assert v.available and v.mode == "live" and v.at == t("09:31:30")
    assert v.sentiment.sealed == 2 and v.sentiment.limit_down == 1
    a, b = v.cards["A.SH"], v.cards["B.SZ"]
    assert (a.status, a.height, a.open_times, a.resealed) == ("sealed", 2, 1, True)
    assert a.first_seal == t("09:30:58") and a.name == "甲科技" and a.is_st is False
    assert (b.status, b.height, b.column) == ("sealed", 1, "885001.TI")
    assert [(c.code, c.name, c.sealed_count) for c in v.columns] == [("885001.TI", "机器人", 2)]
    rows = {r.key: r for r in v.rows}
    assert rows["2"].cells["885001.TI"] == ["A.SH"] and rows["2"].yesterday == 1
    assert rows["1"].cells["885001.TI"] == ["B.SZ"]
    groups = {g.level: g for g in v.compare}
    assert [(i.symbol, i.status, i.prediction) for i in groups[1].items] == [
        ("A.SH", "promoted", 0.42)]
    c = groups[2].items[0]
    assert (c.symbol, c.status, round(c.pct, 4)) == ("C.SZ", "limit_down", -0.1)
    assert sh.calls == [PREV] and v.shadow.hits == 1
    assert [e.kind for e in v.timeline] == ["reseal", "seal", "open", "seal"]


def test_replay_uses_only_records_up_to_t(tmp_path):
    build(tmp_path)
    v = reader(tmp_path).view(DAY, t("09:31:20"))
    assert v.at == t("09:31:15")
    a = v.cards["A.SH"]
    assert (a.status, a.open_times, a.resealed, a.height) == ("broken", 1, False, None)
    assert v.cards["B.SZ"].status == "sealed"
    assert [e.kind for e in v.timeline] == ["seal", "open", "seal"]
    early = reader(tmp_path).view(DAY, t("09:31:05"))
    assert set(early.cards) == {"A.SH", "B.SZ"} and early.cards["B.SZ"].status == "rush"


def test_after_compaction_reads_series_including_yesterday_list(tmp_path):
    store = build(tmp_path)
    assert store.compact(DAY, t("15:10:00")) is None
    assert not store.raw_path(DAY).exists()
    v = reader(tmp_path).view()
    groups = {g.level: g for g in v.compare}
    assert groups[2].items[0].status == "limit_down"  # C 未入选，但序列里保留了它
    s = reader(tmp_path).series(DAY, "A.SH")
    assert s.available and s.limit_up == 11.0 and s.prev_close == 10.0
    assert [p.volume for p in s.points] == [None, 80, 20]
    assert not reader(tmp_path).series(DAY, "Z.SZ").available


def test_final_mode_marks_direct_mismatches_and_uses_today_prediction(tmp_path):
    store = build(tmp_path)
    store.save_reconcile(DAY, [Mismatch("A.SH", "open_times", 1, 2)], t("17:30:00"))
    sh = shadow()
    v = reader(tmp_path, sh=sh).view()
    assert v.mode == "final" and v.mismatch_count == 1
    assert v.cards["A.SH"].diffs == {"open_times": 2}
    assert sh.calls == [DAY] and v.shadow.hits is None
    assert reader(tmp_path, today=date(2026, 11, 14)).view().mode == "history"


def test_unknown_inputs_stay_unknown(tmp_path):
    build(tmp_path, concepts=False, heights=False)
    v = reader(tmp_path).view()
    assert [c.name for c in v.columns] == ["全部（题材不可用）"]
    assert all(c.height is None for c in v.cards.values())
    assert {r.key for r in v.rows} == {"1", "unknown", "other"}
    assert v.compare == [] and v.curves == []


def test_missing_store_and_unknown_day(tmp_path):
    assert not reader(tmp_path).view().available
    build(tmp_path)
    v = reader(tmp_path).view(date(2026, 10, 1))
    assert not v.available and v.days == [DAY]


def test_reader_never_writes(tmp_path):
    build(tmp_path)
    before = (tmp_path / "board.sqlite3").read_bytes()
    reader(tmp_path).view()
    reader(tmp_path).series(DAY, "A.SH")
    assert (tmp_path / "board.sqlite3").read_bytes() == before
    con = sqlite3.connect(tmp_path / "board.sqlite3")
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def _client(warehouse, board):
    config = WebConfig()
    monitor = MonitorService(SessionJournal(warehouse), config=config)
    return TestClient(create_app(monitor, config, limit_up_board=board))


def test_page_and_api_routes(warehouse, tmp_path):
    build(tmp_path)
    client = _client(warehouse, reader(tmp_path))
    page = client.get("/market/limit-up-board")
    assert page.status_code == 200 and "打板看板" in page.text and "题材梯队" in page.text
    assert 'href="/market/limit-up-board"' in client.get("/market/limit-up-shadow").text
    api = client.get("/api/market/limit-up-board", params={"at": "2026-11-13T09:31:20"})
    body = api.json()
    assert api.headers["cache-control"] == "no-store"
    assert body["available"] and body["at"] == "2026-11-13T09:31:15"
    series = client.get("/api/market/limit-up-board/series",
                        params={"day": "2026-11-13", "symbol": "A.SH"}).json()
    assert series["available"] and len(series["points"]) == 3


def test_unconfigured_board_is_unavailable(warehouse):
    client = _client(warehouse, None)
    assert client.get("/api/market/limit-up-board").json()["available"] is False
    assert client.get("/market/limit-up-board").status_code == 200
