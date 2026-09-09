"""入场判定：check_low_open / check_left_pressure / check_high_open。"""

from datetime import date, timedelta

import polars as pl

from qsys.strategy.structural.entry import (
    check_high_open,
    check_left_pressure,
    check_low_open,
)


def history(closes, highs=None, lows=None, moneys=None, volumes=None, opens=None):
    n = len(closes)
    start = date(2024, 1, 1)
    return pl.DataFrame(
        {
            "dt": [start + timedelta(days=i) for i in range(n)],
            "open": opens or list(closes),
            "high": highs or [c * 1.02 for c in closes],
            "low": lows or [c * 0.98 for c in closes],
            "close": list(closes),
            "volume": volumes or [1000] * n,
            "amount": moneys or [2e8] * n,
        }
    )


# ---------------- check_low_open ----------------


def test_low_open_needs_60_days():
    assert check_low_open(history([10.0] * 59), auction_price=9.6) is False


def test_low_open_accepts_a_valid_setup():
    """相对位置低、成交额足、竞价低开 3~4.5%。"""
    closes = [12.0] * 59 + [10.0]
    h = history(closes, highs=[14.0] * 60, lows=[9.5] * 60, moneys=[2e8] * 60)
    # rp = (10 - 9.5) / (14 - 9.5) = 0.111 < 0.5
    assert check_low_open(h, auction_price=10.0 * 0.96) is True


def test_low_open_rejects_high_relative_position():
    closes = [12.0] * 59 + [13.9]
    h = history(closes, highs=[14.0] * 60, lows=[9.5] * 60)
    assert check_low_open(h, auction_price=13.9 * 0.96) is False


def test_low_open_rejects_thin_turnover():
    closes = [12.0] * 59 + [10.0]
    h = history(
        closes, highs=[14.0] * 60, lows=[9.5] * 60, moneys=[2e8] * 59 + [0.9e8]
    )
    assert check_low_open(h, auction_price=9.6) is False


def test_low_open_rejects_a_gap_outside_the_band():
    closes = [12.0] * 59 + [10.0]
    h = history(closes, highs=[14.0] * 60, lows=[9.5] * 60)
    assert check_low_open(h, auction_price=10.0 * 0.99) is False  # 只跌 1%
    assert check_low_open(h, auction_price=10.0 * 0.90) is False  # 跌太多


def test_low_open_band_edges_are_inclusive():
    """区间两端都是闭的。用 1000 做基数，比值可精确表示。"""
    closes = [1200.0] * 59 + [1000.0]
    h = history(closes, highs=[1400.0] * 60, lows=[950.0] * 60)
    assert check_low_open(h, auction_price=955.0) is True
    assert check_low_open(h, auction_price=970.0) is True
    assert check_low_open(h, auction_price=954.0) is False
    assert check_low_open(h, auction_price=971.0) is False


def test_low_open_rejects_a_flat_range():
    h = history([10.0] * 60, highs=[10.0] * 60, lows=[10.0] * 60)
    assert check_low_open(h, auction_price=9.6) is False


def test_low_open_without_an_auction_price_is_rejected():
    closes = [12.0] * 59 + [10.0]
    h = history(closes, highs=[14.0] * 60, lows=[9.5] * 60)
    assert check_low_open(h, auction_price=None) is False


# ---------------- check_left_pressure ----------------


def test_left_pressure_needs_volume_expansion():
    """最后一天的量必须超过窗口内其余最大量的 0.9 倍。"""
    h = history(
        [10.0] * 101,
        highs=[10.0] * 100 + [20.0],
        volumes=[1000] * 100 + [2000],
    )
    assert check_left_pressure(h) is True


def test_left_pressure_rejects_shrinking_volume():
    h = history(
        [10.0] * 101,
        highs=[10.0] * 100 + [20.0],
        volumes=[5000] * 100 + [1000],
    )
    assert check_left_pressure(h) is False


def test_left_pressure_window_stops_at_the_prior_high():
    """窗口自「上一个不低于昨日最高的日子」起算，再往前 5 天。"""
    h = history(
        [10.0] * 101,
        highs=[10.0] * 95 + [30.0] + [10.0] * 4 + [20.0],
        volumes=[9000] * 91 + [1000] * 9 + [1500],
    )
    # 前高在第 96 根 -> zyts_0=5 -> 窗口 10 根，回溯不到更早的 9000 巨量
    assert check_left_pressure(h) is True


def test_left_pressure_needs_at_least_two_bars_in_the_window():
    h = history([10.0], highs=[20.0], volumes=[1000])
    assert check_left_pressure(h) is False


# ---------------- check_high_open ----------------


def valuation(market_cap=100.0, circulating=300.0):
    return {"market_cap": market_cap, "circulating_market_cap": circulating}


def high_history():
    """volume 单位是**手**：VWAP = 11e8 / (1e6 x 100) = 11.0。

    相对前日收盘 10.0 是 +10%，过 7% 阈值。
    """
    return history(
        [10.0, 11.0],
        highs=[10.0, 11.5],
        lows=[10.0, 10.5],
        moneys=[5e8, 11e8],
        volumes=[5e5, 1e6],
    )


def test_high_open_accepts_a_valid_setup():
    assert (
        check_high_open(
            high_history(),
            auction_price=11.0 * 1.03,
            auction_volume=1e6 * 0.05,
            valuation=valuation(),
        )
        is True
    )


def test_high_open_rejects_weak_average_increase():
    """VWAP = 10.5e8 / (1e6 x 100) = 10.5，相对 10.0 只涨 5%，不足 7%。"""
    h = history([10.0, 11.0], moneys=[5e8, 10.5e8], volumes=[5e5, 1e6])
    assert (
        check_high_open(
            h, auction_price=11.0 * 1.03, auction_volume=1e6 * 0.05,
            valuation=valuation(),
        )
        is False
    )


def test_high_open_rejects_money_below_the_band():
    h = history([10.0, 11.0], moneys=[5e8, 3e8], volumes=[5e5, 1e6])
    assert (
        check_high_open(
            h, auction_price=11.0 * 1.03, auction_volume=1e6 * 0.05,
            valuation=valuation(),
        )
        is False
    )


def test_high_open_rejects_money_above_the_band():
    h = history([10.0, 11.0], moneys=[5e8, 25e8], volumes=[5e5, 1e6])
    assert (
        check_high_open(
            h, auction_price=11.0 * 1.03, auction_volume=1e6 * 0.05,
            valuation=valuation(),
        )
        is False
    )


def test_high_open_rejects_small_market_cap():
    assert (
        check_high_open(
            high_history(), auction_price=11.0 * 1.03, auction_volume=1e6 * 0.05,
            valuation=valuation(market_cap=69.9),
        )
        is False
    )


def test_high_open_rejects_large_circulating_cap():
    assert (
        check_high_open(
            high_history(), auction_price=11.0 * 1.03, auction_volume=1e6 * 0.05,
            valuation=valuation(circulating=520.1),
        )
        is False
    )


def test_high_open_rejects_thin_auction():
    assert (
        check_high_open(
            high_history(), auction_price=11.0 * 1.03,
            auction_volume=1e6 * 0.02, valuation=valuation(),
        )
        is False
    )


def test_high_open_requires_a_gap_up_below_six_percent():
    assert (
        check_high_open(
            high_history(), auction_price=11.0,
            auction_volume=1e6 * 0.05, valuation=valuation(),
        )
        is False
    )
    assert (
        check_high_open(
            high_history(), auction_price=11.0 * 1.06,
            auction_volume=1e6 * 0.05, valuation=valuation(),
        )
        is False
    )


def test_high_open_without_valuation_is_rejected():
    """没有市值数据不能放行——原策略 turn.empty 时直接 return False。"""
    assert (
        check_high_open(
            high_history(), auction_price=11.0 * 1.03,
            auction_volume=1e6 * 0.05, valuation=None,
        )
        is False
    )


def test_high_open_without_auction_volume_is_rejected():
    assert (
        check_high_open(
            high_history(), auction_price=11.0 * 1.03,
            auction_volume=None, valuation=valuation(),
        )
        is False
    )


def test_high_open_needs_two_days_of_history():
    h = history([11.0], moneys=[11e8], volumes=[1e6])
    assert (
        check_high_open(
            h, auction_price=11.0 * 1.03, auction_volume=1e6 * 0.05,
            valuation=valuation(),
        )
        is False
    )


def test_high_open_can_skip_the_auction_volume_filter():
    """近似版：竞价量缺失时跳过那一条，而不是整只剔除。

    分钟线只覆盖最近约一年。不给这个口子，HIGH 分支就完全落不到早期区间，
    对账时会表现为「漏掉全部 HIGH 票」——那是数据缺失的假象。
    """
    assert (
        check_high_open(
            high_history(), auction_price=11.0 * 1.03, auction_volume=None,
            valuation=valuation(), require_auction_volume=False,
        )
        is True
    )


def test_skipping_the_filter_does_not_bypass_the_other_conditions():
    """跳过竞价量不等于放行——其余条件照查。"""
    assert (
        check_high_open(
            high_history(), auction_price=11.0 * 1.03, auction_volume=None,
            valuation=valuation(market_cap=69.9), require_auction_volume=False,
        )
        is False
    )


def test_a_present_auction_volume_is_still_checked_in_loose_mode():
    """有数据就还是要查——近似只针对「没有」，不是「不查」。"""
    assert (
        check_high_open(
            high_history(), auction_price=11.0 * 1.03, auction_volume=1e6 * 0.01,
            valuation=valuation(), require_auction_volume=False,
        )
        is False
    )


def test_low_open_uses_exactly_sixty_bars_for_the_range():
    """相对位置只看最近 60 根，多给的历史不能参与计算。

    原策略是 attribute_history(stock, 60, ...)。与聚宽对账时这个 bug 让
    25 条本该选中的候选被漏掉——更早的低点会把相对位置整个抬上去，
    实测有一例 60 根算 0.276、101 根算 0.603。
    """
    # 最近 60 根：低点 9.5、高点 14.0、收盘 10.0 -> rp = 0.111 通过
    # 更早 41 根：低点 2.0 -> 若参与计算 rp = (10-2)/(14-2) = 0.667 拒绝
    old = [10.0] * 41
    recent = [12.0] * 59 + [10.0]
    h = history(
        old + recent,
        highs=[14.0] * 101,
        lows=[2.0] * 41 + [9.5] * 60,
        moneys=[2e8] * 101,
    )
    assert check_low_open(h, auction_price=9.6) is True


def test_low_open_still_needs_sixty_bars():
    h = history([10.0] * 59, highs=[14.0] * 59, lows=[9.5] * 59)
    assert check_low_open(h, auction_price=9.6) is False
