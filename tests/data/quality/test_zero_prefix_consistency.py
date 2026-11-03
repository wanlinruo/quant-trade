from datetime import date, datetime, timedelta

import polars as pl
import pytest

from qsys.data.quality.minute_padding import zero_prefix_issues

DAY = date(2026, 11, 3)


def frames(symbol="920026.BJ", source="qmt"):
    minute = pl.DataFrame({
        "symbol": [symbol] * 3,
        "ts": [datetime(2026, 11, 3, 9, 30) + timedelta(minutes=i) for i in range(3)],
        "open": [10.0, 10.0, 9.7], "high": [10.0, 10.0, 9.8],
        "low": [10.0, 10.0, 9.7], "close": [10.0, 10.0, 9.8],
        "volume": [0.0, 0.0, 10.0], "amount": [0.0, 0.0, 9750.0],
        "source": [source] * 3,
    })
    daily = pl.DataFrame({"symbol": [symbol], "dt": [DAY], "open": [9.7],
                          "pre_close": [10.0], "volume": [10.0]})
    return minute, daily


@pytest.mark.parametrize("source", ["qmt", "paid", "tushare", "joinquant"])
def test_valid_zero_prefix_is_value_based_and_does_not_depend_on_origin(source):
    m, d = frames(source=source)
    assert zero_prefix_issues(m, d).height == 0
    assert zero_prefix_issues(m.sample(fraction=1, shuffle=True, seed=4), d).height == 0


@pytest.mark.parametrize("column,value,reason", [
    ("volume", None, "missing_or_nonfinite_prefix_value"),
    ("volume", -1.0, "nonzero_prefix_volume_or_amount"),
    ("amount", None, "missing_or_nonfinite_prefix_value"),
    ("amount", 1.0, "nonzero_prefix_volume_or_amount"),
    ("high", 11.0, "invalid_or_nonflat_prefix_price"),
    ("open", float("nan"), "missing_or_nonfinite_prefix_value"),
    ("close", float("inf"), "missing_or_nonfinite_prefix_value"),
])
def test_invalid_prefix_still_produces_a_failure(column, value, reason):
    m, d = frames()
    m = m.with_columns(pl.when(pl.col("ts") == m["ts"][0]).then(value).otherwise(pl.col(column)).alias(column))
    assert reason in zero_prefix_issues(m, d)["reason"][0]


@pytest.mark.parametrize("column,value,reason", [
    ("pre_close", None, "missing_or_ambiguous_daily_reference"),
    ("pre_close", 0.0, "invalid_daily_reference"),
    ("pre_close", 10.000000000000002, "prefix_reference_price_mismatch"),
    ("open", 9.700000000000001, "first_traded_open_mismatch"),
    ("volume", 0.0, "first_traded_open_mismatch"),
])
def test_missing_or_different_direct_reference_is_not_rounded_or_defaulted(column, value, reason):
    m, d = frames()
    d = d.with_columns(pl.lit(value).alias(column))
    assert reason in zero_prefix_issues(m, d)["reason"][0]


def test_missing_duplicate_daily_reference_and_duplicate_minutes_fail():
    m, d = frames()
    assert "missing_or_ambiguous" in zero_prefix_issues(m, d.clear())["reason"][0]
    assert "missing_or_ambiguous" in zero_prefix_issues(m, pl.concat([d, d]))["reason"][0]
    assert "duplicate_timestamp" in zero_prefix_issues(pl.concat([m, m.head(1)]), d)["reason"][0]


def test_daily_trades_require_positive_minutes_but_real_zero_trade_day_is_not_filled():
    m, d = frames()
    m = m.with_columns(pl.lit(0.0).alias("volume"), pl.lit(0.0).alias("amount"),
                       *[pl.lit(10.0).alias(c) for c in ("open", "high", "low", "close")])
    assert "missing_positive_volume" in zero_prefix_issues(m, d)["reason"][0]
    d = d.with_columns(pl.lit(0.0).alias("volume"), pl.lit(0.0).alias("open"))
    assert zero_prefix_issues(m, d).height == 0


def test_symbol_days_are_independent():
    m, d = frames()
    second = m.with_columns((pl.col("ts") + pl.duration(days=1)).alias("ts"))
    result = zero_prefix_issues(pl.concat([m, second]), d)
    assert result.height == 1 and result["day"][0] == DAY + timedelta(days=1)
