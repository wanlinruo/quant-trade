from datetime import datetime

import pytest

from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.strategy.base import Strategy
from qsys.strategy.context import PortfolioView
from qsys.strategy.orders import OrderApi

CMB = Symbol("600036", Market.SH)
T = datetime(2024, 1, 3, 15, 0)


def api(held: int = 0, cash: float = 100_000.0, price: float = 10.0) -> OrderApi:
    p = Portfolio(cash)
    if held:
        p.apply_fill(Fill("o", CMB, Side.BUY, held, price, 0.0, T))
        p.unfreeze()
    return OrderApi(PortfolioView.of(p, {CMB: price}), {CMB: price})


def test_submit_queues_an_intent():
    a = api()
    a.submit(CMB, Side.BUY, 100)
    intents = a.drain()
    assert len(intents) == 1
    assert intents[0].qty == 100
    assert intents[0].side is Side.BUY


def test_drain_empties_the_queue():
    a = api()
    a.submit(CMB, Side.BUY, 100)
    a.drain()
    assert a.drain() == ()


def test_submit_limit_order_carries_price():
    a = api()
    a.submit(CMB, Side.BUY, 100, OrderType.LIMIT, 9.5)
    intent = a.drain()[0]
    assert intent.order_type is OrderType.LIMIT
    assert intent.limit_price == 9.5


def test_target_qty_buys_the_difference():
    a = api(held=300)
    a.target_qty(CMB, 500)
    intent = a.drain()[0]
    assert intent.side is Side.BUY
    assert intent.qty == 200


def test_target_qty_sells_the_difference():
    a = api(held=500)
    a.target_qty(CMB, 300)
    intent = a.drain()[0]
    assert intent.side is Side.SELL
    assert intent.qty == 200


def test_target_qty_no_change_emits_nothing():
    a = api(held=500)
    a.target_qty(CMB, 500)
    assert a.drain() == ()


def test_target_qty_zero_liquidates():
    a = api(held=500)
    a.target_qty(CMB, 0)
    intent = a.drain()[0]
    assert intent.side is Side.SELL
    assert intent.qty == 500


def test_target_value_converts_by_price():
    a = api(price=10.0)
    a.target_value(CMB, 5000.0)
    assert a.drain()[0].qty == 500


def test_target_weight_uses_total_value():
    a = api(price=10.0)
    a.target_weight(CMB, 0.10)
    assert a.drain()[0].qty == 1000


def test_target_weight_accounts_for_existing_holding():
    a = api(held=500, price=10.0)
    a.target_weight(CMB, 0.20)
    intent = a.drain()[0]
    assert intent.side is Side.BUY
    assert intent.qty == 1500


def test_target_weight_rejects_out_of_range():
    a = api()
    with pytest.raises(ValueError, match="weight must be in"):
        a.target_weight(CMB, 1.5)


def test_target_without_a_price_raises():
    a = OrderApi(PortfolioView.of(Portfolio(100_000.0), {}), {})
    with pytest.raises(KeyError, match="no price"):
        a.target_value(CMB, 1000.0)


def test_submit_rejects_non_positive_qty():
    a = api()
    with pytest.raises(ValueError, match="qty must be positive"):
        a.submit(CMB, Side.BUY, 0)


def test_strategy_hooks_default_to_no_op():
    class Empty(Strategy):
        pass

    s = Empty()
    s.on_init(None)
    s.on_session_start(None)
    s.on_bar(None, {})
    s.on_fill(None, None)
    s.on_session_end(None)


def test_strategy_can_be_unit_tested_without_a_backtest():
    """显式注入换来的收益：塞假 ctx，断言下单意图。"""

    class BuyOnce(Strategy):
        def on_bar(self, ctx, bars):
            ctx.order.submit(CMB, Side.BUY, 100)

    a = api()

    class FakeCtx:
        order = a

    BuyOnce().on_bar(FakeCtx(), {})
    assert a.drain()[0].qty == 100
