from datetime import datetime

import pytest

from qsys.core.portfolio import Portfolio, Position
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)
PAB = Symbol("000001", Market.SZ)
NOW = datetime(2024, 1, 2, 15, 0)


def buy(qty: int, price: float, fee: float = 5.0, symbol: Symbol = CMB) -> Fill:
    return Fill("o", symbol, Side.BUY, qty, price, fee, NOW)


def sell(qty: int, price: float, fee: float = 5.0, symbol: Symbol = CMB) -> Fill:
    return Fill("o", symbol, Side.SELL, qty, price, fee, NOW)


def test_new_portfolio_holds_cash_only():
    p = Portfolio(100_000.0)
    assert p.cash == 100_000.0
    assert p.initial_cash == 100_000.0
    assert p.positions == {}


def test_position_of_unheld_symbol_is_empty():
    p = Portfolio(100_000.0)
    pos = p.position(CMB)
    assert pos.total_qty == 0
    assert pos.available_qty == 0
    assert pos.is_empty is True


def test_buy_reduces_cash_by_gross_plus_fee():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    assert p.cash == 100_000.0 - 2758.00 - 5.0


def test_buy_increases_total_but_not_available():
    """T+1 的物理基础：买入的股份未解冻前不可卖。"""
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    pos = p.position(CMB)
    assert pos.total_qty == 100
    assert pos.available_qty == 0


def test_unfreeze_makes_all_shares_available():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    p.unfreeze()
    assert p.position(CMB).available_qty == 100


def test_cost_basis_includes_fee():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    assert p.position(CMB).cost_basis == 2763.00
    assert p.position(CMB).avg_cost == 27.63


def test_second_buy_accumulates_cost_basis():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 10.00, fee=5.0))
    p.apply_fill(buy(100, 20.00, fee=5.0))
    pos = p.position(CMB)
    assert pos.total_qty == 200
    assert pos.cost_basis == 3010.00
    assert pos.avg_cost == 15.05


def test_sell_increases_cash_net_of_fee():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    p.unfreeze()
    cash_before = p.cash
    p.apply_fill(sell(100, 30.00))
    assert p.cash == cash_before + 3000.00 - 5.0


def test_sell_reduces_total_and_available():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(200, 27.58))
    p.unfreeze()
    p.apply_fill(sell(50, 30.00))
    pos = p.position(CMB)
    assert pos.total_qty == 150
    assert pos.available_qty == 150


def test_sell_reduces_cost_basis_proportionally():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(200, 10.00, fee=0.0))
    p.unfreeze()
    p.apply_fill(sell(50, 30.00, fee=0.0))
    assert p.position(CMB).cost_basis == 1500.00


def test_selling_everything_drops_the_position():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    p.unfreeze()
    p.apply_fill(sell(100, 30.00))
    assert CMB not in p.positions


def test_selling_more_than_held_raises():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    p.unfreeze()
    with pytest.raises(ValueError, match="cannot sell 200"):
        p.apply_fill(sell(200, 30.00))


def test_market_value_sums_positions():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58, symbol=CMB))
    p.apply_fill(buy(200, 10.00, symbol=PAB))
    assert p.market_value({CMB: 30.0, PAB: 11.0}) == 3000.0 + 2200.0


def test_market_value_requires_a_price_for_every_holding():
    """缺价必须报错——静默跳过会让组合市值被低估。"""
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    with pytest.raises(KeyError, match="600036.SH"):
        p.market_value({})


def test_total_value_is_cash_plus_market_value():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    assert p.total_value({CMB: 30.0}) == p.cash + 3000.0


def test_empty_portfolio_market_value_is_zero():
    assert Portfolio(100_000.0).market_value({}) == 0.0


def test_avg_cost_of_empty_position_is_zero():
    assert Position(CMB).avg_cost == 0.0


def test_position_returned_is_a_snapshot_not_a_handle():
    """外部拿到的持仓不应能改写账户内部状态。"""
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    snapshot = p.position(CMB)
    snapshot.total_qty = 999
    assert p.position(CMB).total_qty == 100
