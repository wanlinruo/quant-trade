"""分钟线质量校验：与日线跨源交叉验证。"""

from datetime import date, datetime

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.quality.minute_rules import check_minute_bars
from qsys.data.storage import Warehouse

DAY = date(2024, 1, 2)


def seed(warehouse: Warehouse, write_daily_bars, minutes, daily=None, symbol="600036.SH"):
    """minutes: [(hh, mm, o, h, l, c, vol)]；daily: (o,h,l,c,vol) 或 None"""
    n = len(minutes)
    warehouse.write(
        "minute_bars",
        pa.table(
            {
                "symbol": [symbol] * n,
                "dt": [datetime(2024, 1, 2, m[0], m[1]) for m in minutes],
                "open": [float(m[2]) for m in minutes],
                "high": [float(m[3]) for m in minutes],
                "low": [float(m[4]) for m in minutes],
                "close": [float(m[5]) for m in minutes],
                "volume": [int(m[6]) for m in minutes],
                "amount": [0.0] * n,
            },
            schema=schema.MINUTE_BARS,
        ),
        year=2024,
    )
    if daily is not None:
        write_daily_bars(warehouse, pl.DataFrame({
            "symbol": [symbol],
            "dt": [DAY],
            "open": [float(daily[0])],
            "high": [float(daily[1])],
            "low": [float(daily[2])],
            "close": [float(daily[3])],
            "volume": [float(daily[4])],
            "amount": [0.0],
        }))


def rules_of(issues):
    return {i.rule for i in issues}


def test_clean_data_has_no_issues(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [(9, 31, 10.0, 10.2, 9.9, 10.1, 600), (15, 0, 10.1, 10.3, 10.0, 10.2, 400)],
        daily=(10.0, 10.3, 9.9, 10.2, 1000),
    )
    assert check_minute_bars(warehouse, DAY) == []


def test_ohlc_inconsistency_is_flagged(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars, [(9, 31, 10.0, 9.0, 9.9, 10.1, 600)])  # high < open
    assert "ohlc" in rules_of(check_minute_bars(warehouse, DAY))


def test_duplicate_timestamps_are_flagged(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [(9, 31, 10.0, 10.2, 9.9, 10.1, 600), (9, 31, 10.0, 10.2, 9.9, 10.1, 600)],
    )
    assert "duplicate_dt" in rules_of(check_minute_bars(warehouse, DAY))


def test_volume_mismatch_against_the_daily_bar_is_flagged(warehouse, write_daily_bars):
    """分钟线来自 QMT、日线来自 mootdx——两个独立源对不上就是有问题。"""
    seed(
        warehouse, write_daily_bars,
        [(9, 31, 10.0, 10.2, 9.9, 10.1, 600)],
        daily=(10.0, 10.3, 9.9, 10.2, 5000),
    )
    assert "volume_vs_daily" in rules_of(check_minute_bars(warehouse, DAY))


def test_small_volume_drift_is_tolerated(warehouse, write_daily_bars):
    """两源对竞价、盘后固定价的归属口径可能略有不同，1% 内不报。"""
    seed(
        warehouse, write_daily_bars,
        [(9, 31, 10.0, 10.3, 9.9, 10.2, 995)],
        daily=(10.0, 10.3, 9.9, 10.2, 1000),
    )
    assert "volume_vs_daily" not in rules_of(check_minute_bars(warehouse, DAY))


def test_high_mismatch_against_the_daily_bar_is_flagged(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [(9, 31, 10.0, 10.2, 9.9, 10.1, 1000)],
        daily=(10.0, 99.0, 9.9, 10.1, 1000),
    )
    assert "high_vs_daily" in rules_of(check_minute_bars(warehouse, DAY))


def test_first_bar_open_must_equal_the_daily_open(warehouse, write_daily_bars):
    """竞价成交价即开盘价，2-1a 已实测。对不上说明时间戳约定错了。"""
    seed(
        warehouse, write_daily_bars,
        [(9, 31, 88.0, 88.0, 88.0, 88.0, 1000)],
        daily=(10.0, 88.0, 9.9, 88.0, 1000),
    )
    assert "first_bar_open" in rules_of(check_minute_bars(warehouse, DAY))


def test_timestamps_outside_trading_hours_are_flagged(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars, [(3, 15, 10.0, 10.2, 9.9, 10.1, 600)])
    assert "outside_session" in rules_of(check_minute_bars(warehouse, DAY))


def test_lunch_break_timestamps_are_flagged(warehouse, write_daily_bars):
    """11:30–13:00 不开盘，出现在这里说明时间戳有问题。"""
    seed(warehouse, write_daily_bars, [(12, 15, 10.0, 10.2, 9.9, 10.1, 600)])
    assert "outside_session" in rules_of(check_minute_bars(warehouse, DAY))


def test_afternoon_bars_are_accepted(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [(13, 1, 10.0, 10.2, 9.9, 10.1, 600), (15, 0, 10.1, 10.3, 10.0, 10.2, 400)],
        daily=(10.0, 10.3, 9.9, 10.2, 1000),
    )
    assert "outside_session" not in rules_of(check_minute_bars(warehouse, DAY))


def test_missing_daily_bar_skips_the_cross_checks(warehouse, write_daily_bars):
    """没有日线可比不是分钟线的错，不该报。"""
    seed(warehouse, write_daily_bars, [(9, 31, 10.0, 10.2, 9.9, 10.1, 600)], daily=None)
    assert rules_of(check_minute_bars(warehouse, DAY)) == set()


def test_no_minute_data_yields_no_issues(warehouse, write_daily_bars):
    assert check_minute_bars(warehouse, DAY) == []


def test_the_0930_auction_bar_is_not_outside_the_session(warehouse, write_daily_bars):
    """09:30 是开盘集合竞价，正常数据，不该被判成时段外。"""
    seed(
        warehouse, write_daily_bars,
        [(9, 30, 41.01, 41.01, 41.01, 41.01, 2195), (15, 0, 40.9, 40.9, 40.9, 40.9, 8221)],
        daily=(41.01, 41.01, 40.9, 40.9, 10416),
    )
    assert "outside_session" not in rules_of(check_minute_bars(warehouse, DAY))


def test_zero_volume_bars_are_accepted(warehouse, write_daily_bars):
    """收盘竞价时段的 14:58/14:59 成交量为 0，四价相等，是正常的。"""
    seed(
        warehouse, write_daily_bars,
        [(14, 59, 41.01, 41.01, 41.01, 41.01, 0), (15, 0, 40.9, 40.9, 40.9, 40.9, 100)],
        daily=(41.01, 41.01, 40.9, 40.9, 100),
    )
    assert check_minute_bars(warehouse, DAY) == []
