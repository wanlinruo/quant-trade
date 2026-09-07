"""引擎级合成数据断言。

每个场景的预期值都是手算的。断言失败时**先手算核对再改代码**——
这些数字是验收基准，不得为了让测试通过而调整预期。
"""

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from qsys.backtest.corporate import CorporateAction
from qsys.backtest.engine import BacktestEngine
from qsys.backtest.fees import AShareFeeModel, ZeroFeeModel
from qsys.backtest.slippage import NoSlippage
from qsys.core.calendar import AShareCalendar
from qsys.core.trading import Side
from qsys.core.types import Market, Symbol
from qsys.markets.ashare import ashare_pipeline
from qsys.strategy.base import Strategy

CMB = Symbol("600036", Market.SH)

BAR_SCHEMA = {
    "symbol": pl.Utf8, "dt": pl.Date, "open": pl.Float64, "high": pl.Float64,
    "low": pl.Float64, "close": pl.Float64, "volume": pl.Int64, "amount": pl.Float64,
}


class SyntheticFeed:
    """按 as_of 过滤的最小 DataFeed，语义与 HistoricalFeed 一致。"""

    def __init__(self, rows):
        self._rows = rows

    def get(self, symbols, start, end, *, as_of, adjustment=None):
        out = []
        for session, (o, h, l, c, v) in sorted(self._rows.items()):
            if not (start <= session <= end):
                continue
            if as_of < datetime.combine(session, datetime.min.time()).replace(hour=15):
                continue
            out.append({"symbol": str(CMB), "dt": session, "open": o, "high": h,
                        "low": l, "close": c, "volume": v, "amount": c * v})
        return pl.DataFrame(out, schema=BAR_SCHEMA)


def flat_series(n: int, price: float = 10.0, volume: int = 10_000_000):
    sessions = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    return sessions, {s: (price, price, price, price, volume) for s in sessions}


def build(strategy, sessions, rows, *, cash=100_000.0, fees=None, actions=None,
          volume_cap=0.05):
    cal = AShareCalendar(sessions)
    return BacktestEngine(
        strategy, feed=SyntheticFeed(rows), calendar=cal, universe=[CMB],
        initial_cash=cash, pipeline=ashare_pipeline(),
        fees=fees or ZeroFeeModel(), slippage=NoSlippage(),
        volume_cap=volume_cap, corporate_actions=actions,
    ).run(sessions[0], sessions[-1])


class BuyOnFirstBar(Strategy):
    def __init__(self, qty: int = 1000):
        self._qty = qty

    def on_bar(self, ctx, bars):
        if not ctx.state.get("done"):
            ctx.state["done"] = True
            ctx.order.submit(CMB, Side.BUY, self._qty)


def test_buy_and_hold_on_a_one_percent_daily_riser():
    """10 天每天涨 1%，第 2 天开盘买入并持有，净值必须等于手算值。"""
    n = 11
    sessions = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    rows = {}
    price = 100.0
    for s in sessions:
        rows[s] = (price, price, price, price, 10_000_000)
        price = round(price * 1.01, 6)

    result = build(BuyOnFirstBar(qty=100), sessions, rows, cash=1_000_000.0)

    assert result.fills[0].price == pytest.approx(101.0)
    final_price = 100.0 * (1.01 ** 10)
    expected = 1_000_000.0 - 101.0 * 100 + final_price * 100
    assert result.final_value == pytest.approx(expected, abs=0.02)


def test_signal_bar_price_is_never_used_for_the_fill():
    """D0 收盘 100、D1 开盘 105 —— 成交价必须是 105，不是 100。

    价差刻意压在涨跌停带内：跳空过大会被 PriceLimitConstraint 正确拒单，
    那样就测不到「用哪根 bar 成交」这件事了。
    """
    sessions = [date(2024, 1, 2), date(2024, 1, 3)]
    rows = {sessions[0]: (100.0, 100.0, 100.0, 100.0, 10_000_000),
            sessions[1]: (105.0, 105.0, 105.0, 105.0, 10_000_000)}
    result = build(BuyOnFirstBar(qty=100), sessions, rows, cash=1_000_000.0)
    assert result.fills[0].price == 105.0


def test_limit_up_on_fill_day_blocks_the_buy():
    sessions = [date(2024, 1, 2), date(2024, 1, 3)]
    rows = {sessions[0]: (10.0, 10.0, 10.0, 10.0, 10_000_000),
            sessions[1]: (11.0, 11.0, 11.0, 11.0, 10_000_000)}
    result = build(BuyOnFirstBar(qty=100), sessions, rows)
    assert result.fills == ()
    assert any("limit up" in r.reason for r in result.rejects)


def test_suspension_on_fill_day_rejects_the_order():
    sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    rows = {sessions[0]: (10.0, 10.0, 10.0, 10.0, 10_000_000),
            sessions[2]: (10.0, 10.0, 10.0, 10.0, 10_000_000)}
    result = build(BuyOnFirstBar(qty=100), sessions, rows)
    assert result.fills == ()
    assert any("suspend" in r.reason for r in result.rejects)


def test_t_plus_one_blocks_same_session_round_trip():
    class SellImmediately(Strategy):
        def on_bar(self, ctx, bars):
            if not ctx.state.get("bought"):
                ctx.state["bought"] = True
                ctx.order.submit(CMB, Side.BUY, 100)

        def on_fill(self, ctx, fill):
            if fill.side is Side.BUY:
                ctx.order.submit(CMB, Side.SELL, fill.qty)

    sessions, rows = flat_series(4)
    result = build(SellImmediately(), sessions, rows)
    buys = [f for f in result.fills if f.side is Side.BUY]
    sells = [f for f in result.fills if f.side is Side.SELL]
    assert len(buys) == 1 and len(sells) == 1
    assert sells[0].filled_at.date() > buys[0].filled_at.date()


def test_dividend_keeps_total_value_flat():
    sessions, rows = flat_series(4)
    actions = {sessions[3]: [CorporateAction(CMB, sessions[3], 0.5, 0.0)]}
    result = build(BuyOnFirstBar(qty=1000), sessions, rows, actions=actions)
    assert result.final_value == pytest.approx(100_000.0 + 500.0)


def test_stock_split_doubles_shares_and_halves_price():
    sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
    rows = {
        sessions[0]: (20.0, 20.0, 20.0, 20.0, 10_000_000),
        sessions[1]: (20.0, 20.0, 20.0, 20.0, 10_000_000),
        sessions[2]: (10.0, 10.0, 10.0, 10.0, 10_000_000),
        sessions[3]: (10.0, 10.0, 10.0, 10.0, 10_000_000),
    }
    actions = {sessions[2]: [CorporateAction(CMB, sessions[2], 0.0, 1.0)]}
    result = build(BuyOnFirstBar(qty=1000), sessions, rows, actions=actions)
    assert result.final_value == pytest.approx(100_000.0)


def test_lot_size_rounds_the_order_down():
    sessions, rows = flat_series(3)
    result = build(BuyOnFirstBar(qty=250), sessions, rows)
    assert result.fills[0].qty == 200


def test_volume_cap_limits_a_large_order():
    sessions, rows = flat_series(3, volume=1_000)
    result = build(BuyOnFirstBar(qty=100_000), sessions, rows, cash=10_000_000.0)
    assert result.fills == ()
    assert result.rejects != ()


def test_volume_cap_partial_fill_is_reported():
    sessions, rows = flat_series(3, volume=100_000)
    result = build(BuyOnFirstBar(qty=100_000), sessions, rows, cash=10_000_000.0)
    assert result.fills[0].qty == 5000
    assert any("volume cap" in r.reason for r in result.rejects)


def test_fees_are_deducted_exactly():
    """1000 股 @10.00：gross 10000，佣金 max(3.0, 5.0)=5.0，过户费 0.10 -> 5.10。"""
    sessions, rows = flat_series(3)
    result = build(BuyOnFirstBar(qty=1000), sessions, rows, fees=AShareFeeModel())
    assert result.fills[0].fee == 5.10
    assert result.final_value == pytest.approx(100_000.0 - 5.10)


def test_no_signal_means_no_change():
    sessions, rows = flat_series(5)
    result = build(Strategy(), sessions, rows)
    assert result.final_value == 100_000.0
    assert result.fills == ()


def test_equity_curve_is_recorded_every_session():
    sessions, rows = flat_series(5)
    result = build(BuyOnFirstBar(qty=100), sessions, rows)
    assert result.equity.height == 5
    assert result.equity["dt"].to_list() == sessions
