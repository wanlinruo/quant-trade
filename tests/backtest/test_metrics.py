from datetime import date, timedelta

import polars as pl
import pytest

from qsys.backtest.metrics import compute_metrics


def equity(values: list[float]) -> pl.DataFrame:
    # 用 timedelta 递增，避免超过 31 个数据点时 date(2024, 1, d+1) 越界
    return pl.DataFrame(
        {
            "dt": [date(2024, 1, 1) + timedelta(days=d) for d in range(len(values))],
            "cash": [0.0] * len(values),
            "market_value": values,
            "total_value": values,
        },
        schema={"dt": pl.Date, "cash": pl.Float64, "market_value": pl.Float64,
                "total_value": pl.Float64},
    )


def test_total_return_on_a_doubling():
    assert compute_metrics(equity([100.0, 200.0])).total_return == pytest.approx(1.0)


def test_total_return_on_a_loss():
    assert compute_metrics(equity([100.0, 50.0])).total_return == pytest.approx(-0.5)


def test_flat_curve_has_zero_return_and_volatility():
    m = compute_metrics(equity([100.0] * 10))
    assert m.total_return == 0.0
    assert m.volatility == 0.0
    assert m.max_drawdown == 0.0


def test_max_drawdown_on_a_peak_and_trough():
    m = compute_metrics(equity([100.0, 120.0, 90.0, 110.0]))
    assert m.max_drawdown == pytest.approx(0.25)


def test_max_drawdown_days_counts_the_longest_underwater_run():
    m = compute_metrics(equity([100.0, 120.0, 90.0, 95.0, 130.0]))
    assert m.max_drawdown_days == 2


def test_monotonic_rise_has_no_drawdown():
    assert compute_metrics(equity([100.0, 110.0, 120.0])).max_drawdown == 0.0


def test_annualized_return_of_a_full_year():
    values = [100.0 * (2 ** (i / 243)) for i in range(244)]
    m = compute_metrics(equity(values), periods_per_year=243)
    assert m.annualized_return == pytest.approx(1.0, abs=0.02)


def test_sharpe_of_a_constant_positive_return():
    """恒定收益 -> 波动率为噪声量级 -> 夏普定义为 0。

    若守卫写成 volatility > 0，浮点噪声（~1e-15）会让夏普算出 1e14
    这种看似天才实则毫无意义的数字，直接污染绩效报告。
    """
    values = [100.0 * (1.001 ** i) for i in range(50)]
    m = compute_metrics(equity(values))
    assert m.volatility < 1e-9
    assert m.sharpe == 0.0


def test_sharpe_is_positive_for_a_rising_noisy_curve():
    values = [100.0]
    for i in range(60):
        values.append(values[-1] * (1.002 if i % 3 else 0.999))
    assert compute_metrics(equity(values)).sharpe > 0


def test_trade_stats_from_fills():
    from datetime import datetime

    from qsys.core.trading import Fill, Side
    from qsys.core.types import Market, Symbol

    cmb = Symbol("600036", Market.SH)
    t = datetime(2024, 1, 2, 9, 30)
    fills = (
        Fill("a", cmb, Side.BUY, 100, 10.0, 0.0, t),
        Fill("b", cmb, Side.SELL, 100, 12.0, 0.0, t),
        Fill("c", cmb, Side.BUY, 100, 12.0, 0.0, t),
        Fill("d", cmb, Side.SELL, 100, 11.0, 0.0, t),
    )
    m = compute_metrics(equity([100.0, 110.0]), fills)
    assert m.n_trades == 4
    assert m.win_rate == pytest.approx(0.5)


def test_no_fills_yields_zero_trade_stats():
    m = compute_metrics(equity([100.0, 110.0]))
    assert m.n_trades == 0
    assert m.win_rate == 0.0
    assert m.turnover == 0.0


def test_single_point_curve_is_all_zeros():
    m = compute_metrics(equity([100.0]))
    assert m.total_return == 0.0
    assert m.max_drawdown == 0.0


def test_empty_curve_does_not_crash():
    m = compute_metrics(equity([]))
    assert m.total_return == 0.0
