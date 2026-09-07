from datetime import date

import polars as pl
import pytest

from qsys.backtest.compare import compare_equity, compare_prices, format_report


def prices(closes: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["600036.SH"] * len(closes),
            "dt": [date(2024, 1, i + 1) for i in range(len(closes))],
            "close": closes,
        }
    )


def equity(values: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "dt": [date(2024, 1, i + 1) for i in range(len(values))],
            "total_value": values,
        }
    )


def test_identical_prices_have_no_mismatch():
    d = compare_prices(prices([10.0, 11.0]), prices([10.0, 11.0]))
    assert d.n_mismatched == 0
    assert d.mismatch_rate == 0.0
    assert d.max_abs_diff == 0.0


def test_price_diff_within_tolerance_is_not_a_mismatch():
    d = compare_prices(prices([10.000]), prices([10.004]), tolerance=0.005)
    assert d.n_mismatched == 0


def test_price_diff_beyond_tolerance_is_flagged():
    d = compare_prices(prices([10.0, 11.0]), prices([10.0, 11.5]), tolerance=0.005)
    assert d.n_mismatched == 1
    assert d.max_abs_diff == pytest.approx(0.5)
    assert d.mismatch_rate == pytest.approx(0.5)


def test_price_samples_are_returned_for_inspection():
    d = compare_prices(prices([10.0, 11.0]), prices([10.0, 11.5]))
    assert d.samples.height == 1
    assert "close_ours" in d.samples.columns
    assert "close_theirs" in d.samples.columns


def test_rows_present_on_only_one_side_are_counted():
    d = compare_prices(prices([10.0, 11.0]), prices([10.0]))
    assert d.n_rows == 1


def test_identical_equity_has_zero_diff():
    d = compare_equity(equity([100.0, 110.0]), equity([100.0, 110.0]))
    assert d.final_diff_pct == 0.0
    assert d.max_diff_pct == 0.0
    assert d.correlation == pytest.approx(1.0)


def test_equity_final_diff_is_relative():
    d = compare_equity(equity([100.0, 110.0]), equity([100.0, 100.0]))
    assert d.final_value_ours == 110.0
    assert d.final_value_theirs == 100.0
    assert d.final_diff_pct == pytest.approx(0.10)


def test_equity_max_diff_finds_the_worst_day():
    d = compare_equity(equity([100.0, 150.0, 110.0]), equity([100.0, 100.0, 110.0]))
    assert d.max_diff_pct == pytest.approx(0.50)


def test_correlation_of_opposite_curves_is_negative():
    d = compare_equity(equity([100.0, 110.0, 120.0]), equity([100.0, 90.0, 80.0]))
    assert d.correlation < 0


def test_report_mentions_all_three_layers():
    pd_ = compare_prices(prices([10.0]), prices([10.0]))
    ed = compare_equity(equity([100.0, 110.0]), equity([100.0, 110.0]))
    text = format_report(pd_, ed)
    assert "L1" in text and "L2" in text
    assert "0" in text


def test_report_flags_unexplained_residual():
    pd_ = compare_prices(prices([10.0]), prices([10.0]))
    ed = compare_equity(equity([100.0, 200.0]), equity([100.0, 100.0]))
    text = format_report(pd_, ed)
    assert "未通过" in text or "FAIL" in text


def test_self_comparison_is_a_clean_baseline():
    """把结果和自己比，必须零差异——这是工具本身的自测。"""
    e = equity([100.0, 105.0, 103.0, 120.0])
    d = compare_equity(e, e)
    assert d.final_diff_pct == 0.0
    assert d.max_diff_pct == 0.0
