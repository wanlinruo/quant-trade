from datetime import date, datetime

from qsys.data.board.concepts import ConceptSnapshot
from qsys.data.board.live import (
    BELOW, NO_LIMIT, OPEN, SEALED, UNKNOWN, BoardDay, limit_up_price, seal_state,
)
from qsys.live.messages import MarketQuote

DAY = date(2026, 11, 13)


def q(symbol, hhmmss, price, *, high=None, prev=10.0, bid1=None, bidv=0, askv=0):
    h, m, s = (int(x) for x in hhmmss.split(":"))
    at = datetime(2026, 11, 13, h, m, s)
    return MarketQuote(symbol=symbol, at=at, price=price, open=prev, high=high or price,
                       low=prev, prev_close=prev, bid1=price if bid1 is None else bid1,
                       bid1_volume=bidv, ask1_volume=askv)


def t(hhmmss):
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return datetime(2026, 11, 13, h, m, s)


LIMITS = {"A.SH": (11.0, 9.0), "B.SZ": (11.0, 9.0), "C.SZ": (11.0, 9.0),
          "N.SH": (1_000_000.0, 0.01), "X.BJ": (13.0, 7.0)}


def test_seal_state_requires_bid_queue_at_limit_and_no_ask():
    assert seal_state(q("A.SH", "09:31:00", 11.0, bidv=500, askv=0), 11.0) == SEALED
    assert seal_state(q("A.SH", "09:31:00", 11.0, bidv=500, askv=3), 11.0) == OPEN
    # 盘口两侧都为 0：无法区分字段缺失与真实为 0（#647），不判封板
    assert seal_state(q("A.SH", "09:31:00", 11.0, bidv=0, askv=0), 11.0) == UNKNOWN
    assert seal_state(q("A.SH", "09:31:00", 10.9, bidv=500), 11.0) == BELOW
    assert seal_state(q("A.SH", "09:31:00", 11.0, bidv=500), None) == NO_LIMIT


def test_unbounded_sentinel_is_no_limit():
    assert limit_up_price(LIMITS, "N.SH") is None
    assert limit_up_price(LIMITS, "MISSING.SZ") is None
    assert limit_up_price(LIMITS, "A.SH") == 11.0


def test_first_seal_open_and_reseal_sequence():
    board = BoardDay(DAY, LIMITS, {"A.SH": 1}, None)
    events, s1 = board.ingest(t("09:31:00"), {"A.SH": q("A.SH", "09:30:57", 11.0, bidv=900)})
    assert [e.kind for e in events] == ["enter", "seal"]
    assert board.tracked["A.SH"].first_seal == datetime(2026, 11, 13, 9, 30, 57)
    assert s1.sealed == 1 and s1.max_height == 2 and s1.promote_1_2 == (1, 1)
    events, s2 = board.ingest(t("09:31:15"), {"A.SH": q("A.SH", "09:31:12", 10.95, high=11.0)})
    assert [e.kind for e in events] == ["open"] and s2.broken == 1 and s2.sealed == 0
    events, _ = board.ingest(t("09:31:30"), {"A.SH": q("A.SH", "09:31:27", 11.0, bidv=100)})
    assert [e.kind for e in events] == ["reseal"]
    assert board.tracked["A.SH"].open_times == 1
    assert board.tracked["A.SH"].first_seal == datetime(2026, 11, 13, 9, 30, 57)


def test_unknown_book_neither_opens_nor_reseals():
    board = BoardDay(DAY, LIMITS, {}, None)
    board.ingest(t("09:31:00"), {"A.SH": q("A.SH", "09:31:00", 11.0, bidv=900)})
    events, s = board.ingest(t("09:31:15"), {"A.SH": q("A.SH", "09:31:15", 11.0)})
    assert events == [] and s.seal_unknown == 1 and s.sealed == 0
    events, _ = board.ingest(t("09:31:30"), {"A.SH": q("A.SH", "09:31:30", 11.0, bidv=5)})
    assert events == [] and board.tracked["A.SH"].open_times == 0


def test_unknown_book_never_counts_as_first_seal():
    board = BoardDay(DAY, LIMITS, {}, None)
    events, s = board.ingest(t("09:31:00"), {"A.SH": q("A.SH", "09:31:00", 11.0)})
    assert [e.kind for e in events] == ["enter"]
    assert board.tracked["A.SH"].first_seal is None and board.ever_sealed == set()


def test_duplicate_and_older_published_at_are_ignored():
    board = BoardDay(DAY, LIMITS, {}, None)
    snap = {"A.SH": q("A.SH", "09:31:00", 11.0, bidv=1)}
    assert board.ingest(t("09:31:00"), snap) is not None
    assert board.ingest(t("09:31:00"), snap) is None
    assert board.ingest(t("09:30:45"), snap) is None
    assert board.ingest(datetime(2026, 11, 12, 15, 0), snap) is None


def test_gap_over_45s_is_recorded_not_filled():
    board = BoardDay(DAY, LIMITS, {}, None)
    board.ingest(t("09:31:00"), {})
    events, _ = board.ingest(t("09:32:00"), {})
    assert [(e.kind, e.detail) for e in events] == [("gap", "09:31:00→09:32:00")]


def test_rush_enters_and_stays_after_falling_back():
    board = BoardDay(DAY, LIMITS, {}, None)
    events, s = board.ingest(t("09:40:00"), {"B.SZ": q("B.SZ", "09:40:00", 10.75)})
    assert [(e.kind, e.detail) for e in events] == [("enter", "rush")] and s.rushing == 1
    board.ingest(t("09:40:15"), {"B.SZ": q("B.SZ", "09:40:15", 10.1, high=10.75)})
    assert "B.SZ" in board.tracked


def test_missing_limit_and_stale_quotes():
    board = BoardDay(DAY, LIMITS, {}, None)
    stale = q("C.SZ", "09:31:00", 11.0, bidv=1).model_copy(
        update={"at": datetime(2026, 11, 12, 15, 0)})
    _, s = board.ingest(t("09:31:00"), {"N.SH": q("N.SH", "09:31:00", 30.0, prev=10.0),
                                        "C.SZ": stale})
    assert s.missing_limit == 1 and s.quotes == 1 and "C.SZ" not in board.tracked


def test_unknown_previous_height_gives_unknown_today_height():
    board = BoardDay(DAY, LIMITS, {"A.SH": None}, None)
    _, s = board.ingest(t("09:31:00"), {"A.SH": q("A.SH", "09:31:00", 11.0, bidv=1)})
    assert board.height("A.SH") is None and s.max_height is None


def test_columns_follow_cumulative_sealed_counts_and_bj_excluded():
    snap = ConceptSnapshot(DAY, datetime(2026, 11, 13, 8, 40),
                           {"885001.TI": frozenset({"A.SH", "B.SZ", "X.BJ"}),
                            "885002.TI": frozenset({"A.SH", "C.SZ"})},
                           {"885001.TI": 3, "885002.TI": 2}, "s" * 64)
    board = BoardDay(DAY, LIMITS, {}, snap)
    board.ingest(t("09:31:00"), {"C.SZ": q("C.SZ", "09:31:00", 11.0, bidv=1),
                                 "X.BJ": q("X.BJ", "09:31:00", 13.0, bidv=1)})
    assert board.tracked["C.SZ"].column == "885002.TI"
    assert board.tracked["X.BJ"].column == "其他"
    board.ingest(t("09:31:15"), {"A.SH": q("A.SH", "09:31:15", 11.0, bidv=1)})
    assert board.tracked["A.SH"].column == "885002.TI"  # 885002 有 2 家（含 A），885001 仅 1 家
