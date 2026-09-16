from datetime import datetime, timedelta

from qsys.backtest.fees import ZeroFeeModel
from qsys.backtest.slippage import NoSlippage
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Symbol
from qsys.live.messages import MarketQuote
from qsys.markets.ashare import ashare_pipeline
from qsys.paper.broker import PaperBroker


SYM = Symbol.parse("600036.SH")
NOW = datetime(2026, 9, 16, 9, 30)


def quote(*, at=NOW + timedelta(seconds=1), price=10.0, volume=10_000):
    return MarketQuote(symbol=str(SYM), at=at, price=price, open=10, high=10,
                       low=10, prev_close=9.8, volume=volume, amount=100_000)


def broker(cash=100_000):
    portfolio = Portfolio(cash)
    return portfolio, PaperBroker(portfolio, ashare_pipeline(), ZeroFeeModel(),
                                  NoSlippage(), volume_cap=0.05)


def test_market_order_uses_strictly_newer_quote_and_updates_virtual_portfolio():
    portfolio, paper = broker()
    order = Order("o1", SYM, Side.BUY, 100, OrderType.MARKET, None, NOW)
    paper.submit(order)
    paper.on_quote(quote(at=NOW))
    assert paper.poll_fills() == ()
    paper.on_quote(quote(volume=20_000))
    fill = paper.poll_fills()[0]
    assert fill.qty == 100
    assert portfolio.position(SYM).total_qty == 100
    assert portfolio.cash == 99_000


def test_limit_order_waits_until_price_crosses():
    _, paper = broker()
    paper.submit(Order("o1", SYM, Side.BUY, 100, OrderType.LIMIT, 9.9, NOW))
    paper.on_quote(quote(price=10.0))
    assert len(paper.pending()) == 1
    paper.on_quote(quote(at=NOW + timedelta(seconds=2), price=9.8, volume=20_000))
    assert paper.poll_fills()[0].price == 9.8


def test_volume_cap_partially_fills_and_keeps_remainder():
    _, paper = broker()
    paper.submit(Order("o1", SYM, Side.BUY, 1_000, OrderType.MARKET, None, NOW))
    paper.on_quote(quote(volume=10_000))
    assert paper.poll_fills()[0].qty == 500
    assert paper.remaining("o1") == 500
    paper.on_quote(quote(at=NOW + timedelta(seconds=2), volume=20_000))
    assert paper.poll_fills()[0].qty == 500
    assert paper.pending() == ()


def test_end_of_session_cancels_without_liquidating():
    portfolio, paper = broker()
    paper.submit(Order("o1", SYM, Side.BUY, 100, OrderType.MARKET, None, NOW))
    paper.cancel_all(NOW, "session ended")
    assert paper.pending() == ()
    assert paper.poll_rejects()[0].reason == "session ended"
    assert portfolio.cash == 100_000
