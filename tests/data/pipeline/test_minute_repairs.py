from datetime import date, datetime, timedelta

import polars as pl

from qsys.data.pipeline.minute_repairs import (
    compare_with_daily,
    daily_from_minutes,
    expected_joinquant_times,
    expected_minute_times,
    invalid_qmt_price_symbols,
    valid_joinquant_overrides,
    valid_paid_overrides,
)

DAY = date(2026, 5, 15)


def _frame(symbol="300344.SZ"):
    times = expected_minute_times(DAY)
    return pl.DataFrame({
        "symbol": [symbol] * len(times), "dt": times,
        "open": [10.0] * len(times), "high": [10.1] * len(times),
        "low": [9.9] * len(times), "close": [10.0] * len(times),
        "volume": [100] * len(times), "amount": [100_000.0] * len(times),
        "factor": [1.0] * len(times),
    })


def test_finds_qmt_positive_volume_zero_price():
    frame = _frame().with_columns(
        pl.lit(0.0).alias("open"), pl.lit(0.0).alias("high"),
        pl.lit(0.0).alias("low"), pl.lit(0.0).alias("close"),
        pl.lit(0.0).alias("amount"),
    )
    assert invalid_qmt_price_symbols(frame) == ["300344.SZ"]


def test_accepts_only_an_exact_valid_paid_grid():
    good = _frame("300344.SZ")
    shifted = _frame("000004.SZ").with_columns(
        pl.when(pl.col("dt") == expected_minute_times(DAY)[0])
        .then(pl.col("dt") + timedelta(minutes=1))
        .otherwise(pl.col("dt"))
    )
    accepted, rejected = valid_paid_overrides(
        pl.concat([good, shifted]), ["300344.SZ", "000004.SZ", "600000.SH"], DAY
    )
    assert accepted["symbol"].unique().to_list() == ["300344.SZ"]
    assert rejected == {"000004.SZ": "bars=241,unique=240", "600000.SH": "missing"}


def test_accepts_joinquant_native_240_bar_grid_without_fabricating_0930():
    frame = _frame().filter(pl.col("dt").is_in(expected_joinquant_times(DAY))).drop("factor")
    accepted, rejected = valid_joinquant_overrides(frame, ["300344.SZ"], DAY)
    assert rejected == {}
    assert accepted.height == 240
    assert accepted["dt"].min().time().isoformat() == "09:31:00"


def test_joinquant_allows_zero_money_only_for_sub_yuan_odd_lot_rounding():
    frame = _frame().filter(pl.col("dt").is_in(expected_joinquant_times(DAY))).drop("factor")
    first, second = expected_joinquant_times(DAY)[:2]
    frame = frame.with_columns(
        pl.when(pl.col("dt") == first).then(1).otherwise(pl.col("volume")).alias("volume"),
        pl.when(pl.col("dt").is_in([first, second])).then(0.0).otherwise(pl.col("amount"))
        .alias("amount"),
        pl.when(pl.col("dt") == first).then(0.36).otherwise(pl.col("open")).alias("open"),
        pl.when(pl.col("dt") == first).then(0.36).otherwise(pl.col("high")).alias("high"),
        pl.when(pl.col("dt") == first).then(0.36).otherwise(pl.col("low")).alias("low"),
        pl.when(pl.col("dt") == first).then(0.36).otherwise(pl.col("close")).alias("close"),
    )
    accepted, rejected = valid_joinquant_overrides(frame, ["300344.SZ"], DAY)
    assert accepted.is_empty()
    assert rejected == {"300344.SZ": "invalid=1,envelope=0"}


def _daily(**overrides):
    row = {"symbol": "300344.SZ", "dt": DAY, "open": 10.0, "high": 10.1, "low": 9.9,
           "close": 10.0, "volume": 241.0, "amount": 241 * 100_000.0}
    row.update(overrides)
    return pl.DataFrame([row])


def test_consistent_minutes_match_daily():
    result = compare_with_daily(_frame(), _daily())
    assert result.select("gross", "minor").rows() == [(None, False)]


def test_volume_inflated_with_unchanged_amount_is_gross():
    """2026-03-05 000004.SZ：分钟成交量合计是日线的 77 倍，成交额却一致。"""
    inflated = _frame().with_columns(pl.col("volume") * 77)
    assert compare_with_daily(inflated, _daily())["gross"].to_list() == ["volume"]


def test_rounding_difference_is_minor_not_gross():
    result = compare_with_daily(_frame(), _daily(volume=240.94))
    assert result.select("gross", "minor").rows() == [(None, True)]


def test_missing_daily_row_is_gross():
    result = compare_with_daily(_frame(), _daily(symbol="600000.SH"))
    assert result["gross"].to_list() == ["daily_missing"]


def test_zero_volume_placeholder_bar_does_not_set_the_open():
    """退市整理首日 09:30 零成交 bar 沿用昨收 5.03，真实开盘是 09:31 的 1.01。"""
    first = expected_minute_times(DAY)[0]
    frame = _frame().with_columns(
        pl.when(pl.col("dt") == first).then(0).otherwise(pl.col("volume")).alias("volume"),
        pl.when(pl.col("dt") == first).then(0.0).otherwise(pl.col("amount")).alias("amount"),
        pl.when(pl.col("dt") == first).then(50.3).otherwise(pl.col("open")).alias("open"),
        pl.when(pl.col("dt") == first).then(50.3).otherwise(pl.col("high")).alias("high"),
    )
    daily = _daily(volume=240.0, amount=240 * 100_000.0)
    assert daily_from_minutes(frame)["open"].to_list() == [10.0]
    assert compare_with_daily(frame, daily)["gross"].to_list() == [None]


def test_price_far_from_daily_is_gross():
    result = compare_with_daily(_frame(), _daily(close=12.0, high=12.0))
    assert result["gross"].to_list() == ["price"]
