from datetime import datetime, timedelta

from qsys.backtest.fees import ZeroFeeModel
from qsys.backtest.slippage import NoSlippage
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Order, OrderType, Side
from qsys.core.types import Symbol
from qsys.live.messages import MarketQuote
from qsys.markets.ashare import ashare_pipeline
from qsys.paper.broker import PaperBroker


SYM = Symbol.parse("600036.SH")
NOW = datetime(2026, 9, 16, 9, 30)
# 当日直取涨跌停价（stk_limit），前收 9.80 主板 10% 档
LIMITS = (10.78, 8.82)


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
    paper.on_quote(limits=LIMITS, quote=quote(at=NOW))
    assert paper.poll_fills() == ()
    paper.on_quote(limits=LIMITS, quote=quote(volume=20_000))
    fill = paper.poll_fills()[0]
    assert fill.qty == 100
    assert portfolio.position(SYM).total_qty == 100
    assert portfolio.cash == 99_000


def test_limit_order_waits_until_price_crosses():
    _, paper = broker()
    paper.submit(Order("o1", SYM, Side.BUY, 100, OrderType.LIMIT, 9.9, NOW))
    paper.on_quote(limits=LIMITS, quote=quote(price=10.0))
    assert len(paper.pending()) == 1
    paper.on_quote(limits=LIMITS, quote=quote(at=NOW + timedelta(seconds=2), price=9.8, volume=20_000))
    assert paper.poll_fills()[0].price == 9.8


def test_volume_cap_partially_fills_and_keeps_remainder():
    _, paper = broker()
    paper.submit(Order("o1", SYM, Side.BUY, 1_000, OrderType.MARKET, None, NOW))
    paper.on_quote(limits=LIMITS, quote=quote(volume=10_000))
    assert paper.poll_fills()[0].qty == 500
    assert paper.remaining("o1") == 500
    paper.on_quote(limits=LIMITS, quote=quote(at=NOW + timedelta(seconds=2), volume=20_000))
    assert paper.poll_fills()[0].qty == 500
    assert paper.pending() == ()


def test_end_of_session_cancels_without_liquidating():
    portfolio, paper = broker()
    paper.submit(Order("o1", SYM, Side.BUY, 100, OrderType.MARKET, None, NOW))
    paper.cancel_all(NOW, "session ended")
    assert paper.pending() == ()
    assert paper.poll_rejects()[0].reason == "session ended"
    assert portfolio.cash == 100_000


def test_a_buy_is_rejected_when_the_direct_limit_price_is_missing():
    """D-19：缺当日直取涨跌停价时买单拒绝，不按板块档位自算。"""
    _, paper = broker()
    paper.submit(Order("o1", SYM, Side.BUY, 100, OrderType.MARKET, None, NOW))
    paper.on_quote(quote(), limits=None)
    assert paper.poll_fills() == ()
    assert "no direct" in paper.poll_rejects()[0].reason


def test_a_buy_at_a_five_percent_st_limit_up_is_rejected():
    _, paper = broker()
    paper.submit(Order("o1", SYM, Side.BUY, 100, OrderType.MARKET, None, NOW))
    paper.on_quote(quote(price=10.29), limits=(10.29, 9.31))
    assert paper.poll_fills() == ()
    assert "limit up" in paper.poll_rejects()[0].reason


def test_a_new_session_resets_the_volume_baseline():
    """2026-09-23：进程自 09-21 运行，基线停在前一日的收盘累计量，

    当日累计量始终没超过它，可成交量上限恒为 0，11:25 的卖单整天没成交。
    """
    portfolio, paper = broker()
    portfolio.apply_fill(
        Fill("seed", SYM, Side.BUY, 2000, 10.0, 0.0, NOW - timedelta(days=2))
    )
    portfolio.unfreeze()
    # 前一交易日收盘：累计 9,000,000 股
    paper.on_quote(quote(volume=9_000_000), limits=(11.0, 9.0))

    paper.begin_session()
    order = Order("o1", SYM, Side.SELL, 2000, OrderType.MARKET, None, NOW)
    paper.submit(order)
    # 当日累计量远小于昨日，但基线已重置：新增量按当日累计量算
    paper.on_quote(quote(volume=3_000_000), limits=(11.0, 9.0))

    assert [f.qty for f in paper.poll_fills()] == [2000]


def test_a_shrinking_cumulative_volume_is_treated_as_a_new_baseline():
    """没走开盘事件（进程盘中重启）时也不能把订单永远挂住。"""
    portfolio, paper = broker()
    portfolio.apply_fill(
        Fill("seed", SYM, Side.BUY, 2000, 10.0, 0.0, NOW - timedelta(days=2))
    )
    portfolio.unfreeze()
    paper.on_quote(quote(volume=9_000_000), limits=(11.0, 9.0))
    paper.submit(Order("o1", SYM, Side.SELL, 2000, OrderType.MARKET, None, NOW))

    paper.on_quote(quote(volume=1_000_000), limits=(11.0, 9.0))

    assert [f.qty for f in paper.poll_fills()] == [2000]
