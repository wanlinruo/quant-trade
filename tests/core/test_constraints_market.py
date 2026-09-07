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


# --- PriceLimitConstraint ---


def test_limit_prices_are_rounded_to_cents():
    up, down = PriceLimitConstraint.limit_prices(9.99, 0.10)
    assert up == 10.99
    assert down == 8.99


def test_limit_prices_on_round_number():
    up, down = PriceLimitConstraint.limit_prices(10.00, 0.10)
    assert up == 11.00
    assert down == 9.00


def test_price_limit_rejects_buy_at_limit_up():
    """涨停时买不到。"""
    c = ConstraintContext(portfolio=holding(0, 0), reference_price=11.00, prev_close=10.00)
    v = PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.BUY, 100), c)
    assert v.is_rejected
    assert "limit up" in v.reason


def test_price_limit_allows_sell_at_limit_up():
    """涨停时卖得掉。"""
    c = ConstraintContext(portfolio=holding(300, 300), reference_price=11.00, prev_close=10.00)
    assert PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.SELL, 100), c).allowed_qty == 100


def test_price_limit_rejects_sell_at_limit_down():
    """跌停时卖不掉。"""
    c = ConstraintContext(portfolio=holding(300, 300), reference_price=9.00, prev_close=10.00)
    v = PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.SELL, 100), c)
    assert v.is_rejected
    assert "limit down" in v.reason


def test_price_limit_allows_buy_at_limit_down():
    """跌停时买得到。"""
    c = ConstraintContext(portfolio=holding(0, 0), reference_price=9.00, prev_close=10.00)
    assert PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100


def test_price_limit_passes_inside_the_band():
    c = ConstraintContext(portfolio=holding(300, 300), reference_price=10.50, prev_close=10.00)
    con = PriceLimitConstraint(LIMITS)
    assert con.apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100
    assert con.apply(OrderIntent(CMB, Side.SELL, 100), c).allowed_qty == 100


def test_price_limit_uses_board_specific_width():
    """创业板 20%：10.00 -> 12.00 才是涨停，11.00 仍可买入。"""
    c = ConstraintContext(
        portfolio=holding(0, 0), reference_price=11.00, prev_close=10.00, board="gem"
    )
    assert PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100


def test_price_limit_falls_back_to_default_for_unknown_board():
    c = ConstraintContext(
        portfolio=holding(0, 0), reference_price=11.00, prev_close=10.00, board="unknown"
    )
    assert PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.BUY, 100), c).is_rejected


def test_price_limit_passes_when_prev_close_unknown():
    """无前收（如上市首日）时不判定，交由其它约束处理。"""
    c = ConstraintContext(portfolio=holding(0, 0), reference_price=11.00, prev_close=None)
    assert PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100
