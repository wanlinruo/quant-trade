from datetime import date, datetime

import polars as pl

from qsys.data.board.reconcile import Mismatch, Observed, reconcile

DAY = date(2026, 11, 12)


def _ll(rows):
    return pl.DataFrame(rows, schema={"symbol": pl.Utf8, "dt": pl.Date, "limit_type": pl.Utf8,
                                      "limit_times": pl.Int64, "open_times": pl.Int64,
                                      "first_time": pl.Utf8})


def test_first_seal_within_one_interval_matches():
    ll = _ll([{"symbol": "A.SH", "dt": DAY, "limit_type": "U", "limit_times": 2,
               "open_times": 0, "first_time": "093100"}])
    ok = Observed("A.SH", 2, datetime(2026, 11, 12, 9, 31, 15), 0)
    late = Observed("A.SH", 2, datetime(2026, 11, 12, 9, 31, 16), 0)
    assert reconcile([ok], ll) == []
    assert reconcile([late], ll) == [Mismatch("A.SH", "first_seal", late.first_seal,
                                              datetime(2026, 11, 12, 9, 31))]


def test_height_and_open_mismatch_reported():
    ll = _ll([{"symbol": "A.SH", "dt": DAY, "limit_type": "U", "limit_times": 3,
               "open_times": 3, "first_time": "093100"}])
    got = reconcile([Observed("A.SH", 2, datetime(2026, 11, 12, 9, 31), 2)], ll)
    assert {(m.field, m.observed, m.direct) for m in got} == {("height", 2, 3), ("open_times", 2, 3)}


def test_null_direct_field_is_not_compared():
    ll = _ll([{"symbol": "A.SH", "dt": DAY, "limit_type": "U", "limit_times": None,
               "open_times": None, "first_time": None},
              {"symbol": "B.SH", "dt": DAY, "limit_type": "Z", "limit_times": 1,
               "open_times": 1, "first_time": "100000"}])
    observed = [Observed("A.SH", 5, datetime(2026, 11, 12, 10), 9),
                Observed("B.SH", 9, None, 0)]
    assert reconcile(observed, ll) == []
