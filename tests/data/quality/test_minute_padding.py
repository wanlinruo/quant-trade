from datetime import datetime

import polars as pl

from qsys.data.quality.minute_padding import leading_zero_cases


def _frame(rows):
    return pl.DataFrame(
        rows,
        schema={
            "symbol": pl.Utf8, "ts": pl.Datetime("us"),
            "volume": pl.Float64, "source": pl.Utf8,
        },
        orient="row",
    )


def test_describes_single_and_multiple_prefixes_without_judging_them():
    frame = _frame([
        ("A", datetime(2026, 1, 2, 9, 30), 0.0, "paid"),
        ("A", datetime(2026, 1, 2, 9, 31), 10.0, "paid"),
        ("B", datetime(2026, 1, 2, 9, 30), 0.0, "qmt"),
        ("B", datetime(2026, 1, 2, 9, 31), 0.0, "qmt"),
        ("B", datetime(2026, 1, 2, 9, 32), 20.0, "qmt"),
    ])

    got = leading_zero_cases(frame)

    assert dict(got.select("symbol", "leading_rows").iter_rows()) == {"A": 1, "B": 2}


def test_zero_after_first_trade_is_not_counted_as_a_prefix():
    frame = _frame([
        ("A", datetime(2026, 1, 2, 9, 30), 10.0, "qmt"),
        ("A", datetime(2026, 1, 2, 9, 31), 0.0, "qmt"),
    ])

    assert leading_zero_cases(frame).is_empty()


def test_all_day_zero_is_outside_this_rule():
    frame = _frame([
        ("A", datetime(2026, 1, 2, 9, 30), 0.0, "qmt"),
        ("A", datetime(2026, 1, 2, 9, 31), 0.0, "qmt"),
    ])

    assert leading_zero_cases(frame).is_empty()
