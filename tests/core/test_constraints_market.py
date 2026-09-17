from datetime import datetime

import pytest

from qsys.core.constraints import (
    ConstraintContext,
    PriceLimitConstraint,
    SettlementConstraint,
    SuspensionConstraint,
)
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderIntent, Side
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)
NOW = datetime(2024, 1, 2, 15, 0)
LIMITS = {"main": 0.10, "gem": 0.20, "star": 0.20, "bse": 0.30}


def holding(total: int, available: int, cash: float = 100_000.0) -> Portfolio:
    p = Portfolio(cash)
    if total:
        p.apply_fill(Fill("o", CMB, Side.BUY, total, 10.0, 0.0, NOW))
        if available:
            p.unfreeze()
            if available < total:
                p.positions[CMB].available_qty = available
    return p


# --- SettlementConstraint（T+1） ---


def test_settlement_allows_any_buy():
    c = ConstraintContext(portfolio=holding(0, 0), reference_price=10.0)
    assert SettlementConstraint().apply(OrderIntent(CMB, Side.BUY, 500), c).allowed_qty == 500


def test_settlement_rejects_sell_of_unsettled_shares():
    """当日买入当日卖出必须被拒绝。"""
    c = ConstraintContext(portfolio=holding(300, 0), reference_price=10.0)
    v = SettlementConstraint().apply(OrderIntent(CMB, Side.SELL, 100), c)
    assert v.is_rejected
    assert "settled" in v.reason


def test_settlement_clamps_sell_to_settled_quantity():
    c = ConstraintContext(portfolio=holding(300, 200), reference_price=10.0)
    v = SettlementConstraint().apply(OrderIntent(CMB, Side.SELL, 300), c)
    assert v.allowed_qty == 200


def test_settlement_passes_fully_settled_sell():
    c = ConstraintContext(portfolio=holding(300, 300), reference_price=10.0)
    assert SettlementConstraint().apply(OrderIntent(CMB, Side.SELL, 300), c).allowed_qty == 300


# --- SuspensionConstraint ---


def test_suspension_rejects_buy():
    c = ConstraintContext(portfolio=holding(0, 0), reference_price=10.0, is_suspended=True)
    v = SuspensionConstraint().apply(OrderIntent(CMB, Side.BUY, 100), c)
    assert v.is_rejected
    assert "suspended" in v.reason


def test_suspension_rejects_sell():
    c = ConstraintContext(portfolio=holding(300, 300), reference_price=10.0, is_suspended=True)
    assert SuspensionConstraint().apply(OrderIntent(CMB, Side.SELL, 100), c).is_rejected


def test_suspension_passes_when_trading():
    c = ConstraintContext(portfolio=holding(0, 0), reference_price=10.0)
    assert SuspensionConstraint().apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100


# --- PriceLimitConstraint：只认当日直取涨跌停价（D-19） ---


def ctx(price, *, up=11.00, down=9.00, held=(0, 0)):
    return ConstraintContext(portfolio=holding(*held), reference_price=price,
                             limit_up=up, limit_down=down)


def test_price_limit_rejects_buy_at_limit_up():
    """涨停时买不到。"""
    v = PriceLimitConstraint().apply(OrderIntent(CMB, Side.BUY, 100), ctx(11.00))
    assert v.is_rejected
    assert "limit up" in v.reason


def test_price_limit_allows_sell_at_limit_up():
    """涨停时卖得掉。"""
    assert PriceLimitConstraint().apply(
        OrderIntent(CMB, Side.SELL, 100), ctx(11.00, held=(300, 300))).allowed_qty == 100


def test_price_limit_rejects_sell_at_limit_down():
    """跌停时卖不掉。"""
    v = PriceLimitConstraint().apply(OrderIntent(CMB, Side.SELL, 100), ctx(9.00, held=(300, 300)))
    assert v.is_rejected
    assert "limit down" in v.reason


def test_price_limit_allows_buy_at_limit_down():
    """跌停时买得到。"""
    assert PriceLimitConstraint().apply(OrderIntent(CMB, Side.BUY, 100), ctx(9.00)).allowed_qty == 100


def test_price_limit_passes_inside_the_band():
    con = PriceLimitConstraint()
    assert con.apply(OrderIntent(CMB, Side.BUY, 100), ctx(10.50)).allowed_qty == 100
    assert con.apply(OrderIntent(CMB, Side.SELL, 100), ctx(10.50, held=(300, 300))).allowed_qty == 100


def test_an_st_stock_sealed_at_its_five_percent_limit_cannot_be_bought():
    """2026-07-06 前主板 ST 为 5% 档。按板块 10% 自算时 10.50 不算涨停、买单照成交。"""
    v = PriceLimitConstraint().apply(OrderIntent(CMB, Side.BUY, 100), ctx(10.50, up=10.50, down=9.50))
    assert v.is_rejected


def test_without_a_direct_limit_price_buys_are_refused():
    """用户 2026-09-17 定：缺直取涨跌停价时买单拒绝——不按板块档位自算。"""
    v = PriceLimitConstraint().apply(OrderIntent(CMB, Side.BUY, 100), ctx(10.00, up=None, down=None))
    assert v.is_rejected
    assert "no direct" in v.reason


def test_without_a_direct_limit_price_sells_pass():
    """缺价时卖单放行：持仓照常能退出，真实跌停由交易所拦截。"""
    assert PriceLimitConstraint().apply(
        OrderIntent(CMB, Side.SELL, 100), ctx(10.00, up=None, down=None, held=(300, 300))
    ).allowed_qty == 100


def test_board_tier_derivation_is_gone():
    assert not hasattr(PriceLimitConstraint, "limit_prices")
