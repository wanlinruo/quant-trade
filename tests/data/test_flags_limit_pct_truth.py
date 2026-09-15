"""`limit_pct` 必须与同一行的 `limit_up` 自洽。

2026-09-15 的直取切换把 `limit_up` 换成了 `price_limits_daily` 的真值，
却把 `limit_pct` 留在旧规则上（「ST 一律 5%」），造成 68,649 行同一行里
两个字段互相矛盾——见 D-13。修法是让 `limit_pct` 由直取涨停价**解码**
得出：试每个已知档位，取能精确还原出 `limit_up` 的那个；一个都还原不出
就记 null，不猜。
"""

from datetime import date

import pyarrow as pa

from qsys.data import schema
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.query import WarehouseQuery

DAY = date(2026, 4, 30)


def _bars(write_daily_bars, warehouse, symbol, pre_close, close):
    write_daily_bars(warehouse, __import__("polars").DataFrame({
        "symbol": [symbol], "dt": [DAY], "name": [None],
        "open": [close], "high": [close], "low": [close], "close": [close],
        "pre_close": [pre_close], "volume": [100.0], "amount": [3e4],
    }))


def _st(warehouse, symbol, is_st):
    warehouse.write("stock_st_daily", pa.table({
        "symbol": [symbol], "dt": [DAY], "is_st": [is_st],
        "name": [None], "st_type": [None], "st_type_name": [None],
    }, schema=schema.STOCK_ST_DAILY), year=2026)


def _limits(warehouse, symbol, pre_close, up, down):
    warehouse.write("price_limits_daily", pa.table({
        "symbol": [symbol], "dt": [DAY], "pre_close": [pre_close],
        "limit_up": [up], "limit_down": [down],
    }, schema=schema.PRICE_LIMITS_DAILY), year=2026)


def _built(warehouse):
    build_daily_flags(warehouse, DAY, DAY)
    return WarehouseQuery(warehouse).daily_flags(DAY, DAY)


def test_a_gem_st_stock_keeps_its_twenty_percent_tier(warehouse, write_daily_bars):
    """创业板 ST 的真实上限是 20%，不是 5%。

    旧规则把它算成 5%，股价涨 5% 就被判成涨停——对账实测因此多报了
    1,466 个 300 号段的假首板。
    """
    _bars(write_daily_bars, warehouse, "300029.SZ", 7.96, 9.55)
    _st(warehouse, "300029.SZ", True)
    _limits(warehouse, "300029.SZ", 7.96, 9.55, 6.37)

    got = _built(warehouse)
    assert got["limit_up"].to_list() == [9.55]
    assert got["limit_pct"].to_list() == [0.20]


def test_a_main_board_st_stock_can_really_be_ten_percent(warehouse, write_daily_bars):
    """主板 ST 也不是一律 5%。

    实测：直取给 10% 档的 7,437 个主板 ST 股-日里，有 238 天真实涨幅
    超过 9.8%——若真是 5% 档不可能发生。
    """
    _bars(write_daily_bars, warehouse, "000010.SZ", 1.68, 1.85)
    _st(warehouse, "000010.SZ", True)
    _limits(warehouse, "000010.SZ", 1.68, 1.85, 1.51)

    got = _built(warehouse)
    assert got["limit_pct"].to_list() == [0.10]


def test_an_undecodable_limit_price_records_null_not_a_guess(warehouse, write_daily_bars):
    """新股首日等无涨跌幅限制的情形还原不出任何已知档位，就记 null。

    回落到板块档位会让这一行看起来「有档位」，而它的 limit_up 根本
    不是那个档位算出来的——那正是 D-13 要消灭的矛盾。
    """
    _bars(write_daily_bars, warehouse, "600519.SH", 10.00, 44.00)
    _st(warehouse, "600519.SH", False)
    _limits(warehouse, "600519.SH", 10.00, 44.00, 1.00)

    got = _built(warehouse)
    assert got["limit_pct"].to_list() == [None]


def test_without_a_direct_price_the_board_tier_still_stands(warehouse, write_daily_bars):
    """没有直取价时走派生路径，limit_pct 仍是板块档位。

    这条守的是修法本身：解码不能把「没有直取价」也一并变成 null。
    """
    _bars(write_daily_bars, warehouse, "600519.SH", 10.00, 11.00)
    _st(warehouse, "600519.SH", False)

    got = _built(warehouse)
    assert got["limit_up"].to_list() == [11.00]
    assert got["limit_pct"].to_list() == [0.10]
