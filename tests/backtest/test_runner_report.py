from datetime import date

import polars as pl

from qsys.backtest.runner import benchmark_nav


def test_benchmark_is_normalized_to_initial_cash():
    equity = pl.DataFrame({"dt": [date(2024, 1, 2), date(2024, 1, 3)],
                           "total_value": [100_000.0, 101_000.0]})
    index = pl.DataFrame({"dt": [date(2024, 1, 2), date(2024, 1, 3)],
                          "close": [3000.0, 3030.0]})
    got = benchmark_nav(equity, index, 100_000)
    assert got["total_value"].to_list() == [100_000.0, 101_000.0]


def test_incomplete_benchmark_is_not_presented_as_comparable():
    equity = pl.DataFrame({"dt": [date(2024, 1, 2), date(2024, 1, 3)],
                           "total_value": [100_000.0, 101_000.0]})
    index = pl.DataFrame({"dt": [date(2024, 1, 2)], "close": [3000.0]})
    assert benchmark_nav(equity, index, 100_000).height == 0
