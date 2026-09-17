"""ST 只认 Tushare `stock_st`，涨跌停价只认 `stk_limit`（D-11）。

名称判 ST、ST 固定 5% 档、按板块档位自算涨停价的路径均已删除。
"""

from datetime import date

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.query import WarehouseQuery

DAYS = [date(2026, 4, 29), date(2026, 4, 30)]


def test_daily_flags_has_truth_columns_not_inference_audit_columns():
    """反推没了，反推的审计列也不该留。"""
    names = set(schema.DAILY_FLAGS.names)
    assert "is_st" in names
    assert "st_source" in names
    assert "st_inferred_raw" not in names
    assert "st_inference_rejected_by_price_evidence" not in names
    assert "st_inferred_final" not in names
    assert schema.DAILY_FLAGS.field("is_st").type == pa.bool_()
    assert schema.DAILY_FLAGS.field("st_source").type == pa.string()


def test_flag_columns_match_the_schema():
    """列清单与 schema 必须一致，否则 cast 会在落库时才炸。"""
    from qsys.data.pipeline.flags import _FLAG_COLUMNS

    assert _FLAG_COLUMNS == list(schema.DAILY_FLAGS.names)


def test_the_name_inference_helpers_are_gone():
    from qsys.data.pipeline import flags, tushare_reference

    for module, name in ((flags, "resolve_names"), (flags, "_assert_name_coverage"),
                         (flags, "ST_LIMIT"), (flags, "limit_prices"),
                         (tushare_reference, "st_on")):
        assert not hasattr(module, name), f"{module.__name__}.{name} 仍在"


def _bars(write_daily_bars, warehouse, rows):
    write_daily_bars(warehouse, pl.DataFrame(rows))


def _one(symbol, day, *, name=None, close=3.0, high=None, pre_close=3.0):
    return {
        "symbol": [symbol], "dt": [day], "name": [name],
        "open": [close], "high": [high if high is not None else close],
        "low": [close], "close": [close], "pre_close": [pre_close],
        "volume": [100.0], "amount": [3e4],
    }


def _flags(warehouse, day):
    return WarehouseQuery(warehouse).daily_flags(day, day).sort("symbol")


def test_st_comes_from_stock_st(warehouse, write_daily_bars, write_direct_truth):
    rows = _one("000010.SZ", DAYS[1], name="美丽生态")
    _bars(write_daily_bars, warehouse, rows)
    write_direct_truth(warehouse, pl.DataFrame(rows), st={"000010.SZ"})
    build_daily_flags(warehouse, DAYS[1], DAYS[1])
    got = _flags(warehouse, DAYS[1])
    assert got["is_st"].to_list() == [True]
    assert got["st_source"].to_list() == ["stock_st"]


def test_a_st_looking_name_does_not_make_a_stock_st(
    warehouse, write_daily_bars, write_direct_truth
):
    """名称里带 ST 不算数，stock_st 说不是就不是。"""
    rows = _one("000010.SZ", DAYS[1], name="*ST美丽")
    _bars(write_daily_bars, warehouse, rows)
    write_direct_truth(warehouse, pl.DataFrame(rows), st=())
    build_daily_flags(warehouse, DAYS[1], DAYS[1])
    assert _flags(warehouse, DAYS[1])["is_st"].to_list() == [False]


def test_missing_stock_st_row_leaves_st_unknown_even_with_an_st_name(
    warehouse, write_daily_bars, write_direct_truth
):
    """stock_st 缺这一行：is_st 与 st_source 都是 null，不从名称兜底。"""
    rows = {
        "symbol": ["000010.SZ", "000011.SZ"], "dt": [DAYS[1], DAYS[1]],
        "name": ["*ST美丽", "*ST缺行"],
        "open": [3.0, 3.0], "high": [3.0, 3.0], "low": [3.0, 3.0],
        "close": [3.0, 3.0], "pre_close": [3.0, 3.0],
        "volume": [100.0, 100.0], "amount": [3e4, 3e4],
    }
    _bars(write_daily_bars, warehouse, rows)
    write_direct_truth(warehouse, pl.DataFrame(rows).filter(pl.col("symbol") == "000010.SZ"),
                       st={"000010.SZ"})
    build_daily_flags(warehouse, DAYS[1], DAYS[1])
    got = _flags(warehouse, DAYS[1])
    assert got["is_st"].to_list() == [True, None]
    assert got["st_source"].to_list() == ["stock_st", None]


def test_st_status_is_per_day(warehouse, write_daily_bars, write_direct_truth):
    rows = {
        "symbol": ["000010.SZ", "000010.SZ"], "dt": DAYS, "name": [None, None],
        "open": [3.0, 3.0], "high": [3.0, 3.0], "low": [3.0, 3.0],
        "close": [3.0, 3.0], "pre_close": [3.0, 3.0],
        "volume": [100.0, 100.0], "amount": [3e4, 3e4],
    }
    _bars(write_daily_bars, warehouse, rows)
    write_direct_truth(warehouse, pl.DataFrame(rows))
    # 000010.SZ 实测 2026-04-30 起戴帽：两天各按当日名单判定，不结转
    warehouse.write("stock_st_daily", pa.table({
        "symbol": ["000010.SZ", "000010.SZ"], "dt": DAYS, "is_st": [False, True],
        "name": [None, None], "st_type": [None, None], "st_type_name": [None, None],
    }, schema=schema.STOCK_ST_DAILY), year=2026)
    build_daily_flags(warehouse, DAYS[0], DAYS[1])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[1]).sort("dt")
    assert got["is_st"].to_list() == [False, True]


def test_limit_price_and_reference_come_from_stk_limit(
    warehouse, write_daily_bars, write_direct_truth
):
    rows = _one("000001.SZ", DAYS[0], close=12.03, pre_close=10.94)
    _bars(write_daily_bars, warehouse, rows)
    write_direct_truth(warehouse, pl.DataFrame(rows))
    build_daily_flags(warehouse, DAYS[0], DAYS[0])
    got = _flags(warehouse, DAYS[0])
    assert got["limit_up"].to_list() == [12.03]
    assert got["adj_prev_close"].to_list() == [10.94]
    assert got["limit_pct"].to_list() == [0.10]
    assert got["is_limit_up"].to_list() == [True]


def test_an_st_stock_takes_whatever_tier_stk_limit_publishes(
    warehouse, write_daily_bars, write_direct_truth
):
    """ST 档位不再写死：2026-07-06 前主板 ST 是 5%，之后是 10%，都由直取价说了算。"""
    rows = _one("000010.SZ", DAYS[1], close=3.15, pre_close=3.0)
    _bars(write_daily_bars, warehouse, rows)
    write_direct_truth(warehouse, pl.DataFrame(rows), tiers={"000010.SZ": 0.05},
                       st={"000010.SZ"})
    build_daily_flags(warehouse, DAYS[1], DAYS[1])
    got = _flags(warehouse, DAYS[1])
    assert got["limit_pct"].to_list() == [0.05]
    assert got["limit_up"].to_list() == [3.15]
    assert got["is_limit_up"].to_list() == [True]


def test_without_stk_limit_an_st_stock_gets_no_invented_limit(
    warehouse, write_daily_bars, write_direct_truth
):
    rows = _one("000010.SZ", DAYS[1], close=3.15, pre_close=3.0)
    _bars(write_daily_bars, warehouse, rows)
    write_direct_truth(warehouse, pl.DataFrame(rows), st={"000010.SZ"})
    warehouse.write("price_limits_daily", schema.PRICE_LIMITS_DAILY.empty_table(), year=2026)
    build_daily_flags(warehouse, DAYS[1], DAYS[1])
    got = _flags(warehouse, DAYS[1])
    assert got["is_st"].to_list() == [True]
    assert got["limit_up"].to_list() == [None]
    assert got["limit_pct"].to_list() == [None]
    assert got["is_limit_up"].to_list() == [None]


def test_board_filter_keeps_beijing_out_of_daily_flags(
    warehouse, write_daily_bars, write_direct_truth
):
    """付费源含北交所 343 只，旧表没有。不显式过滤会静默改变广度因子。"""
    rows = {
        "symbol": ["000001.SZ", "920268.BJ"], "dt": [DAYS[0], DAYS[0]],
        "name": [None, None],
        "open": [10.0, 5.0], "high": [10.0, 5.0], "low": [10.0, 5.0],
        "close": [10.0, 5.0], "pre_close": [10.0, 5.0],
        "volume": [100.0, 100.0], "amount": [1e5, 5e4],
    }
    _bars(write_daily_bars, warehouse, rows)
    write_direct_truth(warehouse, pl.DataFrame(rows), tiers={"920268.BJ": 0.30})
    build_daily_flags(warehouse, DAYS[0], DAYS[0], boards={"main", "gem", "star"})
    assert _flags(warehouse, DAYS[0])["symbol"].to_list() == ["000001.SZ"]


def test_ex_dividend_day_uses_the_direct_reference_price(
    warehouse, write_daily_bars, write_direct_truth
):
    """`prev_close`（昨日收盘）与 `adj_prev_close`（除权参考价）只在除权日不相等。

    第一天收盘 11.30；第二天交易所参考价 10.94。涨停价 12.03 对应 10.94；
    若误用 11.30 会得到 12.43，当日就摸不到涨停。
    """
    rows = {
        "symbol": ["000001.SZ", "000001.SZ"], "dt": DAYS, "name": [None, None],
        "open": [11.30, 11.50], "high": [11.30, 12.03],
        "low": [11.30, 11.40], "close": [11.30, 12.03],
        "pre_close": [None, 10.94],
        "volume": [100.0, 100.0], "amount": [1e5, 1e5],
    }
    _bars(write_daily_bars, warehouse, rows)
    write_direct_truth(warehouse, pl.DataFrame(rows))
    build_daily_flags(warehouse, DAYS[0], DAYS[1])
    got = _flags(warehouse, DAYS[1])
    assert got["prev_close"].to_list() == [11.30]
    assert got["adj_prev_close"].to_list() == [10.94]
    assert got["limit_up"].to_list() == [12.03]
    assert got["is_limit_up"].to_list() == [True]
