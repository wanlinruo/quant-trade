"""打板看板测试夹具（旧页与新前端接口共用，#648 / #662）。"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from qsys.data.board.concepts import ConceptSnapshot
from qsys.data.board.store import BoardStore
from qsys.live.messages import MarketQuote
from qsys.web.limit_up_board import LimitUpBoardReader

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
