from datetime import date, datetime

import pytest

from qsys.backtest.fees import ZeroFeeModel
from qsys.backtest.sim_broker import SimBroker
from qsys.backtest.slippage import FixedRateSlippage, NoSlippage
from qsys.core.events import MarketSnapshot
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Order, OrderType, Side
from qsys.core.types import Bar, Market, Symbol
from qsys.markets.ashare import ashare_pipeline

CMB = Symbol("600036", Market.SH)
D = date(2024, 1, 3)
T = datetime(2024, 1, 2, 15, 0)


def order(side: Side, qty: int, oid: str = "o1") -> Order:
    return Order(oid, CMB, side, qty, OrderType.MARKET, None, T)


def bar(open_: float, volume: int = 1_000_000) -> Bar:
    return Bar(CMB, D, open_, open_ * 1.05, open_ * 0.95, open_ * 1.02, volume, 0.0)


def snapshot(open_: float, *, prev_close: float = 10.0, volume: int = 1_000_000,
             suspended: bool = False) -> MarketSnapshot:
    bars = {} if suspended else {CMB: bar(open_, volume)}
    return MarketSnapshot(session=D, bars=bars, prev_closes={CMB: prev_close},
                          boards={CMB: "main"})


def broker(portfolio: Portfolio, **kw) -> SimBroker:
    defaults = dict(pipeline=ashare_pipeline(), fees=ZeroFeeModel(), slippage=NoSlippage())
    return SimBroker(portfolio, **{**defaults, **kw})


def test_submitted_order_is_pending_until_market_data():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    assert len(b.pending()) == 1
    assert b.poll_fills() == ()


def test_order_fills_at_next_bar_open():
    """信号在 bar T 生成，成交在 bar T+1 开盘——这是不可妥协的原则。"""
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.50))
    fills = b.poll_fills()
    assert len(fills) == 1
    assert fills[0].price == 10.50
    assert fills[0].qty == 100


def test_pending_is_cleared_after_fill():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.50))
    assert b.pending() == ()


def test_poll_fills_drains_the_queue():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.50))
    assert len(b.poll_fills()) == 1
    assert b.poll_fills() == ()


def test_slippage_is_applied_to_fill_price():
    b = broker(Portfolio(100_000.0), slippage=FixedRateSlippage(0.001))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.00))
    assert b.poll_fills()[0].price == 10.01


def test_fee_is_charged_on_fill():
    from qsys.backtest.fees import AShareFeeModel

    b = broker(Portfolio(100_000.0), fees=AShareFeeModel())
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.00))
    assert b.poll_fills()[0].fee == 5.01


def test_suspension_rejects_the_order():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.00, suspended=True))
    assert b.poll_fills() == ()
    rejects = b.poll_rejects()
    assert len(rejects) == 1
    assert "suspend" in rejects[0].reason


def test_limit_up_rejects_buy():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(11.00, prev_close=10.00))
    assert b.poll_fills() == ()
    assert "limit up" in b.poll_rejects()[0].reason


def test_volume_cap_clamps_the_fill():
    """单笔成交不超过当根 bar 成交量的 5%。"""
    b = broker(Portfolio(10_000_000.0), volume_cap=0.05)
    b.submit(order(Side.BUY, 100_000))
    b.on_market_data(snapshot(10.00, volume=1_000_000))
    assert b.poll_fills()[0].qty == 50_000


def test_volume_capped_remainder_is_rejected_not_silently_dropped():
    b = broker(Portfolio(10_000_000.0), volume_cap=0.05)
    b.submit(order(Side.BUY, 100_000))
    b.on_market_data(snapshot(10.00, volume=1_000_000))
    rejects = b.poll_rejects()
    assert len(rejects) == 1
    assert rejects[0].qty == 50_000
    assert "volume cap" in rejects[0].reason


def test_volume_cap_rounds_down_to_lot():
    b = broker(Portfolio(10_000_000.0), volume_cap=0.05)
    b.submit(order(Side.BUY, 100_000))
    b.on_market_data(snapshot(10.00, volume=1_111))
    assert b.poll_fills() == ()
    assert b.poll_rejects() != ()


def test_constraints_clamp_by_cash():
    b = broker(Portfolio(3050.0))
    b.submit(order(Side.BUY, 1000))
    b.on_market_data(snapshot(10.00))
    assert b.poll_fills()[0].qty == 300


def test_sell_without_settled_shares_is_rejected():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("x", CMB, Side.BUY, 500, 10.0, 0.0, T))
    b = broker(p)
    b.submit(order(Side.SELL, 500))
    b.on_market_data(snapshot(10.00))
    assert b.poll_fills() == ()
    assert "settled" in b.poll_rejects()[0].reason


def test_cancel_all_clears_pending():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.cancel_all()
    assert b.pending() == ()
    b.on_market_data(snapshot(10.50))
    assert b.poll_fills() == ()


def test_fill_timestamp_is_the_session_open():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.50))
    assert b.poll_fills()[0].filled_at.date() == D


def test_multiple_orders_fill_independently():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100, "a"))
    b.submit(order(Side.BUY, 200, "b"))
    b.on_market_data(snapshot(10.00))
    assert sorted(f.qty for f in b.poll_fills()) == [100, 200]


def test_broker_does_not_mutate_the_portfolio():
    """撮合只产生 Fill，账户由引擎负责应用——职责分离。"""
    p = Portfolio(100_000.0)
    b = broker(p)
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.50))
    b.poll_fills()
    assert p.cash == 100_000.0
    assert p.positions == {}
