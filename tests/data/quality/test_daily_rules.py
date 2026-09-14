"""日线内部空洞检测。"""

from datetime import date

import polars as pl

from qsys.data.quality.daily_rules import find_holes
from qsys.data.storage import Warehouse

D = [date(2024, 1, d) for d in (2, 3, 4, 5, 8)]


def seed(warehouse: Warehouse, write_daily_bars, rows) -> None:
    """rows: [(symbol, dt)]"""
    n = len(rows)
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "dt": [r[1] for r in rows],
        "open": [10.0] * n, "high": [10.0] * n,
        "low": [10.0] * n, "close": [10.0] * n,
        "volume": [1000.0] * n, "amount": [1e6] * n,
    }))


def test_a_missing_middle_day_is_a_hole(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars, [("600001.SH", D[0]), ("600001.SH", D[2])])

    report = find_holes(warehouse, D[0], D[-1], sessions=D)

    assert report.total == 1
    assert report.stock_days.to_dicts() == [{"symbol": "600001.SH", "dt": D[1]}]
    assert report.symbols == ["600001.SH"]


def test_days_before_listing_are_not_holes(warehouse, write_daily_bars):
    """上市前没有 bar 是正常的，不该报。"""
    seed(warehouse, write_daily_bars, [("600001.SH", D[3]), ("600001.SH", D[4]),
         ("600002.SH", D[0]), ("600002.SH", D[4])])

    report = find_holes(warehouse, D[0], D[-1], sessions=D)

    assert set(report.stock_days["symbol"].to_list()) == {"600002.SH"}


def test_a_complete_series_has_no_holes(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars, [("600001.SH", d) for d in D])
    assert find_holes(warehouse, D[0], D[-1]).total == 0


def test_holes_are_counted_per_day(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars, [("600001.SH", D[0]), ("600001.SH", D[3]),
         ("600002.SH", D[0]), ("600002.SH", D[3])])

    per_day = find_holes(warehouse, D[0], D[-1], sessions=D).per_day.to_dicts()

    assert per_day == [{"dt": D[1], "holes": 2}, {"dt": D[2], "holes": 2}]


def test_star_and_bse_are_excluded_like_the_breadth_universe(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars, [("688001.SH", D[0]), ("688001.SH", D[2]),
         ("430001.SZ", D[0]), ("430001.SZ", D[2])])
    assert find_holes(warehouse, D[0], D[-1]).total == 0


def test_an_empty_warehouse_reports_nothing(warehouse, write_daily_bars):
    assert find_holes(warehouse, D[0], D[-1]).total == 0


def test_the_session_list_can_be_given_explicitly(warehouse, write_daily_bars):
    """全市场并集等于日历只是巧合：整天漏采时并集里就没有那天。"""
    seed(warehouse, write_daily_bars, [("600001.SH", D[0]), ("600001.SH", D[2])])

    without = find_holes(warehouse, D[0], D[-1])           # 并集只有 D[0], D[2]
    with_calendar = find_holes(warehouse, D[0], D[-1], sessions=D)

    assert without.total == 0
    assert with_calendar.stock_days["dt"].to_list() == [D[1]]
