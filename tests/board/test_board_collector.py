import sqlite3
from datetime import date, datetime

import polars as pl
import pytest

from qsys.data.board.collector_cli import Collector
from qsys.data.board.store import BoardStore
from qsys.live.messages import MarketQuote, QuoteSnapshot

DAY, PREV = date(2026, 11, 13), date(2026, 11, 12)


class FakeQuery:
    def __init__(self, *, limits=True, limit_list_prev=True):
        self.limits = limits
        self.limit_list_prev = limit_list_prev
        self.direct_today = pl.DataFrame()

    def trading_calendar(self, start, end, open_only=True):
        return pl.DataFrame({"dt": [date(2026, 11, 11), PREV, DAY]})

    def price_limits(self, symbols, start, end):
        if not self.limits:
            return pl.DataFrame()
        return pl.DataFrame({"symbol": ["A.SH", "B.SZ"], "dt": [DAY, DAY],
                             "limit_up": [11.0, 22.0], "limit_down": [9.0, 18.0]})

    def daily_flags(self, start, end):
        return pl.DataFrame({
            "dt": [date(2026, 11, 11), PREV, date(2026, 11, 11), PREV],
            "symbol": ["A.SH", "A.SH", "B.SZ", "B.SZ"],
            "is_st": [False, False, False, False],
            "is_limit_up": [False, True, False, False]})

    def limit_list(self, symbols, start, end):
        if start == end == DAY:
            return self.direct_today
        if not self.limit_list_prev:
            return pl.DataFrame({"dt": [], "symbol": [], "limit_type": [], "limit_times": []},
                                schema={"dt": pl.Date, "symbol": pl.String,
                                        "limit_type": pl.String, "limit_times": pl.Int64})
        return pl.DataFrame({"dt": [PREV], "symbol": ["A.SH"], "limit_type": ["U"],
                             "limit_times": [1]})


class FakeProvider:
    def __init__(self, fail_concepts=False):
        self.fail = fail_concepts

    def ths_index(self):
        if self.fail:
            raise RuntimeError("积分不足")
        return pl.DataFrame({"ts_code": ["885001.TI", "885338.TI"], "name": ["机器人", "融资融券"],
                             "count": [2, 2], "type": ["N", "N"]})

    def ths_member(self, code):
        return pl.DataFrame({"con_code": ["A.SH", "B.SZ"]})

    def stock_st(self, day):
        return pl.DataFrame()


class FakeTransport:
    value = None

    def get_key(self, key):
        assert key == "state:quote_realtime_latest"
        return self.value


def snapshot(hhmmss, quotes):
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return QuoteSnapshot(request_id="latest", published_at=datetime(2026, 11, 13, h, m, s),
                         quotes=quotes).model_dump_json()


def quote(symbol, hhmmss, price, prev, *, bidv=0, askv=0, high=None):
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return MarketQuote(symbol=symbol, at=datetime(2026, 11, 13, h, m, s), price=price,
                       open=prev, high=high or price, low=prev, prev_close=prev, bid1=price,
                       bid1_volume=bidv, ask1_volume=askv)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 13, 9, 0)

    def __call__(self):
        return self.now

    def at(self, hhmmss):
        h, m, s = (int(x) for x in hhmmss.split(":"))
        self.now = datetime(2026, 11, 13, h, m, s)


def make(tmp_path, query=None, provider=None):
    clock, transport = Clock(), FakeTransport()
    c = Collector(BoardStore(tmp_path), query or FakeQuery(), provider or FakeProvider(),
                  transport, "state:quote_realtime_latest", clock=clock, pause=lambda s: None)
    return c, clock, transport


def run_day(c, clock, transport):
    c.step()  # 09:00 准备
    clock.at("09:31:00")
    transport.value = snapshot("09:31:00", {
        "A.SH": quote("A.SH", "09:30:58", 11.0, 10.0, bidv=800),
        "B.SZ": quote("B.SZ", "09:30:58", 21.0, 20.0)})
    c.step()
    clock.at("09:31:05")
    c.step()  # 同一 published_at 不重复处理
    clock.at("09:31:15")
    transport.value = snapshot("09:31:15", {
        "A.SH": quote("A.SH", "09:31:13", 10.9, 10.0, high=11.0),
        "B.SZ": quote("B.SZ", "09:31:13", 22.0, 20.0, bidv=5)})
    c.step()


def test_full_day_prepare_collect_compact_reconcile(tmp_path):
    c, clock, transport = make(tmp_path)
    run_day(c, clock, transport)
    con = sqlite3.connect(tmp_path / "board.sqlite3")
    assert con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
    kinds = [r[0] for r in con.execute("SELECT kind FROM events ORDER BY seq")]
    assert kinds == ["enter", "seal", "open", "enter", "seal"]
    assert con.execute("SELECT concept FROM board WHERE symbol='A.SH'").fetchone()[0] == "885001.TI"
    # 融资融券在排除名单里，不进入归列候选
    assert con.execute("SELECT DISTINCT concept FROM board").fetchall() == [("885001.TI",)]

    clock.at("15:10:00")
    c.step()
    assert not c.store.raw_path(DAY).exists()
    assert con.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 4
    assert con.execute("SELECT compact_error FROM day_meta").fetchone()[0] is None

    c.query.direct_today = pl.DataFrame({
        "symbol": ["A.SH", "B.SZ"], "limit_type": ["U", "U"], "limit_times": [2, 1],
        "first_time": ["093058", "093113"], "open_times": [2, 0]})
    clock.at("17:30:00")
    c.step()
    mism = con.execute("SELECT symbol, field, observed, direct FROM mismatches").fetchall()
    assert mism == [("A.SH", "open_times", "1", "2")]
    assert c.store.reconciled(DAY)


def test_restart_replays_raw_and_recovers_unsaved_step(tmp_path):
    c, clock, transport = make(tmp_path)
    run_day(c, clock, transport)
    # 模拟上次在"全量已写、派生未存"之间退出：删掉最后一份派生结果
    con = sqlite3.connect(tmp_path / "board.sqlite3")
    with con:
        con.execute("DELETE FROM snapshots WHERE published_at='2026-11-13T09:31:15'")
        con.execute("DELETE FROM events WHERE published_at='2026-11-13T09:31:15'")
    c2, clock2, transport2 = make(tmp_path, query=c.query)
    clock2.at("09:31:20")
    transport2.value = transport.value
    c2.step()
    assert c2.board.tracked["A.SH"].open_times == 1
    assert con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
    clock2.at("15:10:00")
    c2.step()
    assert con.execute("SELECT compact_error FROM day_meta").fetchone()[0] is None


def test_compaction_keeps_raw_when_replay_disagrees(tmp_path):
    c, clock, transport = make(tmp_path)
    run_day(c, clock, transport)
    con = sqlite3.connect(tmp_path / "board.sqlite3")
    with con:
        con.execute("UPDATE snapshots SET sealed = sealed + 1")
    clock.at("15:10:00")
    c.step()
    assert c.store.raw_path(DAY).exists()
    assert "不一致" in con.execute("SELECT compact_error FROM day_meta").fetchone()[0]


def test_waits_for_direct_limits_before_preparing(tmp_path):
    query = FakeQuery(limits=False)
    c, clock, _ = make(tmp_path, query=query)
    c.step()
    assert not c.store.prepared(DAY) and c.status["phase"] == "preparing"
    query.limits = True
    c.step()
    assert c.store.prepared(DAY)


def test_concept_failure_and_missing_prev_list_are_recorded_not_filled(tmp_path):
    c, clock, transport = make(tmp_path, query=FakeQuery(limit_list_prev=False),
                               provider=FakeProvider(fail_concepts=True))
    run_day(c, clock, transport)
    con = sqlite3.connect(tmp_path / "board.sqlite3")
    meta = con.execute("SELECT concept_error, heights_error FROM day_meta").fetchone()
    assert "积分不足" in meta[0] and "limit_list_d 未入库" in meta[1]
    assert con.execute("SELECT DISTINCT concept FROM board").fetchall() == [(None,)]
    row = con.execute("SELECT max_height, p12_from FROM snapshots ORDER BY published_at"
                      ).fetchone()
    assert row == (None, None)


def test_closed_day_does_nothing(tmp_path):
    c, clock, _ = make(tmp_path)
    clock.now = datetime(2026, 11, 14, 10, 0)
    c.step()
    assert c.status["phase"] == "closed" and not (tmp_path / "board.sqlite3").exists()


@pytest.mark.parametrize("hhmmss", ["08:00:00"])
def test_before_concept_window_does_nothing(tmp_path, hhmmss):
    c, clock, _ = make(tmp_path)
    clock.at(hhmmss)
    c.step()
    assert not (tmp_path / "board.sqlite3").exists()
