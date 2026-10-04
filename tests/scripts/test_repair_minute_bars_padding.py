"""剔除 QMT 区间查询产生的昨收价填充行。
见 docs/reference/qmt-range-query-padding.md。"""

from datetime import datetime

import polars as pl

from scripts.repair_minute_bars_padding import drop_padding

COLS = {"symbol": pl.Utf8, "dt": pl.Datetime, "open": pl.Float64,
        "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
        "volume": pl.Int64, "amount": pl.Float64}


def _f(rows):
    return pl.DataFrame(rows, schema=COLS, orient="row")


def test_leading_zero_volume_rows_are_dropped():
    frame = _f([("600000.SH", datetime(2026, 6, 15, 9, 30), 7.1, 7.1, 7.1, 7.1, 0, 0.0),
                ("600000.SH", datetime(2026, 6, 15, 9, 31), 7.1, 7.2, 7.1, 7.2, 300, 2160.0)])
    assert drop_padding(frame)["dt"].to_list() == [datetime(2026, 6, 15, 9, 31)]


def test_zero_volume_after_first_trade_survives():
    frame = _f([("600000.SH", datetime(2026, 6, 15, 9, 30), 7.1, 7.1, 7.1, 7.1, 300, 2130.0),
                ("600000.SH", datetime(2026, 6, 15, 9, 31), 7.1, 7.1, 7.1, 7.1, 0, 0.0)])
    assert drop_padding(frame).height == 2


def test_day_with_no_trade_disappears():
    frame = _f([("600000.SH", datetime(2026, 6, 15, 9, 30), 7.1, 7.1, 7.1, 7.1, 0, 0.0),
                ("600000.SH", datetime(2026, 6, 15, 9, 31), 7.1, 7.1, 7.1, 7.1, 0, 0.0)])
    assert drop_padding(frame).height == 0


def test_volume_total_is_never_changed():
    frame = _f([("600000.SH", datetime(2026, 6, 15, 9, 30), 7.1, 7.1, 7.1, 7.1, 0, 0.0),
                ("600000.SH", datetime(2026, 6, 15, 9, 31), 7.1, 7.2, 7.1, 7.2, 300, 2160.0),
                ("600001.SH", datetime(2026, 6, 15, 9, 30), 5.0, 5.0, 5.0, 5.0, 100, 500.0)])
    assert drop_padding(frame)["volume"].sum() == frame["volume"].sum()


def test_other_symbols_same_day_are_independent():
    frame = _f([("600000.SH", datetime(2026, 6, 15, 9, 30), 7.1, 7.1, 7.1, 7.1, 0, 0.0),
                ("600000.SH", datetime(2026, 6, 15, 9, 31), 7.1, 7.2, 7.1, 7.2, 300, 2160.0),
                ("600001.SH", datetime(2026, 6, 15, 9, 30), 5.0, 5.0, 5.0, 5.0, 100, 500.0)])
    out = drop_padding(frame)
    assert sorted(out["symbol"].to_list()) == ["600000.SH", "600001.SH"]


def test_columns_and_order_are_preserved():
    """落盘要 cast 成 MINUTE_BARS，列名与顺序不能变。"""
    frame = _f([("600000.SH", datetime(2026, 6, 15, 9, 30), 7.1, 7.2, 7.0, 7.2, 300, 2160.0)])
    assert drop_padding(frame).columns == frame.columns
