from datetime import date

import polars as pl

from qsys.data.board.ladder import PromotionRow, previous_heights, promotions, today_height

D1, D2, D3 = date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)
SESSIONS = [D1, D2, D3]


def _ll(rows):
    return pl.DataFrame(rows, schema={"symbol": pl.Utf8, "dt": pl.Date, "limit_type": pl.Utf8,
                                      "limit_times": pl.Int64})


def _flags(rows):
    return pl.DataFrame(rows, schema={"symbol": pl.Utf8, "dt": pl.Date, "is_limit_up": pl.Boolean,
                                      "is_st": pl.Boolean})


def test_non_st_height_from_limit_times():
    ll = _ll([{"symbol": "A.SH", "dt": D2, "limit_type": "U", "limit_times": 3},
              {"symbol": "B.SH", "dt": D2, "limit_type": "Z", "limit_times": 1}])
    flags = _flags([{"symbol": "A.SH", "dt": D2, "is_limit_up": True, "is_st": False},
                    {"symbol": "B.SH", "dt": D2, "is_limit_up": False, "is_st": False}])
    heights = previous_heights(D2, ll, flags, SESSIONS)
    assert heights["A.SH"] == 3 and heights["B.SH"] == 0


def test_st_height_counts_consecutive_flags():
    ll = _ll([{"symbol": "A.SH", "dt": D2, "limit_type": "U", "limit_times": 1}])
    flags = _flags([{"symbol": "S.SZ", "dt": D1, "is_limit_up": True, "is_st": True},
                    {"symbol": "S.SZ", "dt": D2, "is_limit_up": True, "is_st": True}])
    assert previous_heights(D2, ll, flags, [date(2026, 11, 9), D1, D2])["S.SZ"] is None  # 11-09 缺行
    flags = flags.vstack(_flags([{"symbol": "S.SZ", "dt": date(2026, 11, 9), "is_limit_up": False,
                                  "is_st": True}]))
    assert previous_heights(D2, ll, flags, [date(2026, 11, 9), D1, D2])["S.SZ"] == 2


def test_st_gap_makes_height_unknown():
    ll = _ll([{"symbol": "A.SH", "dt": D2, "limit_type": "U", "limit_times": 1}])
    flags = _flags([{"symbol": "S.SZ", "dt": D2, "is_limit_up": True, "is_st": True}])
    assert previous_heights(D2, ll, flags, SESSIONS[:2])["S.SZ"] is None


def test_missing_previous_day_list_makes_heights_unknown():
    flags = _flags([{"symbol": "A.SH", "dt": D2, "is_limit_up": True, "is_st": False}])
    assert previous_heights(D2, _ll([]), flags, SESSIONS)["A.SH"] is None


def test_unknown_previous_never_becomes_first_board():
    assert today_height(None, True) is None
    assert today_height(0, True) == 1 and today_height(2, True) == 3 and today_height(2, False) == 0


def test_promotions_grouped_desc():
    prev = {"A.SH": 3, "B.SH": 1, "C.SH": 1, "D.SH": None, "E.SH": 0}
    rows = promotions(prev, {"A.SH", "C.SH", "D.SH", "E.SH"})
    assert [r.level for r in rows] == [3, 1]
    assert rows[0] == PromotionRow(3, ("A.SH",), ("A.SH",))
    assert rows[1] == PromotionRow(1, ("B.SH", "C.SH"), ("C.SH",))
