"""三接口合成 28 列。单位换算全部实测确认过，不得凭文档假设。"""

from datetime import date

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.pipeline.tushare_daily import compose_daily

DAY = date(2026, 9, 11)


def _daily():
    return pl.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["20260911"],
        "open": [11.76], "high": [11.79], "low": [11.69], "close": [11.70],
        "pre_close": [11.70], "change": [0.0], "pct_chg": [0.0],
        "vol": [582305.98], "amount": [682946.78266],
    })


def _basic():
    return pl.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["20260911"], "close": [11.70],
        "turnover_rate": [0.44], "turnover_rate_f": [1.06], "volume_ratio": [1.0],
        "pe": [5.39], "pe_ttm": [5.29], "pb": [0.49], "ps": [1.75],
        "ps_ttm": [1.73], "dv_ratio": [5.05], "dv_ttm": [5.03],
        "total_share": [1940591.8198],
        "float_share": [1940568.4991],
        "free_share": [816056.5553],
        "total_mv": [22996013.067],
        "circ_mv": [22995736.725],
    })


def _factor():
    return pl.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260911"],
                         "adj_factor": [139.008]})


def test_produces_exactly_the_twenty_eight_columns():
    got = compose_daily(_daily(), _basic(), _factor())
    assert got.columns == list(schema.TUSHARE_DAILY.names)


def test_amount_is_converted_from_thousand_yuan_to_yuan():
    """实测确认：Tushare amount 单位是千元，x1000 后与旧源逐位相同。"""
    got = compose_daily(_daily(), _basic(), _factor())
    assert got["amount"].to_list() == pytest.approx([682946782.66])


def test_volume_stays_in_lots_and_keeps_its_decimals():
    """手数带小数是源的真实精度，取整是有损变换。"""
    got = compose_daily(_daily(), _basic(), _factor())
    assert got["volume"].to_list() == [582305.98]


def test_share_counts_are_converted_from_ten_thousand_to_one():
    got = compose_daily(_daily(), _basic(), _factor())
    assert got["total_shares"].to_list() == [19405918198.0]
    assert got["circulating_shares"].to_list() == [19405684991.0]
    assert got["free_shares"].to_list() == [8160565553.0]


def test_market_caps_are_converted_from_ten_thousand_yuan_to_yuan():
    got = compose_daily(_daily(), _basic(), _factor())
    assert got["market_cap"].to_list() == pytest.approx([229960130670.0])
    assert got["circulating_market_cap"].to_list() == pytest.approx([229957367250.0])


def test_ratios_are_taken_as_is():
    """百分数保持百分数，不除以 100——旧源如此，换算会让两版对不上。"""
    got = compose_daily(_daily(), _basic(), _factor())
    assert got["turnover_ratio"].to_list() == [0.44]
    assert got["pe"].to_list() == [5.39]
    assert got["dividend_yield"].to_list() == [5.05]


def test_total_shares_is_taken_directly_not_derived_from_market_cap():
    """**不得复刻旧源的反算。**

    旧源的 total_shares 是 market_cap / close 反算的，A+H 股因此与 Tushare
    原值相差上亿股（华虹宏力差 1.91 亿）。反算属明令禁止，此处必须直取。
    """
    d, b, f = _daily(), _basic(), _factor()
    b = b.with_columns(pl.lit(192849.4794).alias("total_share"))
    got = compose_daily(d, b, f)
    assert got["total_shares"].to_list() == [1928494794.0]
    assert got["total_shares"].to_list() != [229960130670.0 / 11.70]


def test_symbol_and_date_are_normalised():
    got = compose_daily(_daily(), _basic(), _factor())
    assert got["symbol"].to_list() == ["000001.SZ"]
    assert got["dt"].to_list() == [DAY]


def test_a_symbol_missing_from_daily_basic_gets_nulls_not_zeros():
    """拿不到就记 null。补 0 会让下游把「没有数据」当成「数值为零」。"""
    d = pl.concat([_daily(), _daily().with_columns(pl.lit("600000.SH").alias("ts_code"))])
    got = compose_daily(d, _basic(), _factor())
    row = got.filter(pl.col("symbol") == "600000.SH").to_dicts()[0]
    assert row["pe"] is None
    assert row["total_shares"] is None
    assert row["close"] == 11.70


def test_a_symbol_missing_from_daily_is_dropped_entirely():
    """daily 没有的标的当日没有行情，不能只靠 daily_basic 造出一行。"""
    b = pl.concat([_basic(), _basic().with_columns(pl.lit("600000.SH").alias("ts_code"))])
    got = compose_daily(_daily(), b, _factor())
    assert got["symbol"].to_list() == ["000001.SZ"]


def test_empty_inputs_produce_an_empty_frame_with_the_right_schema():
    empty = _daily().clear()
    got = compose_daily(empty, _basic().clear(), _factor().clear())
    assert got.height == 0
    assert got.columns == list(schema.TUSHARE_DAILY.names)
