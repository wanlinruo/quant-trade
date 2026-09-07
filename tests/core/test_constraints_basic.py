import pytest

from qsys.core.constraints import ConstraintContext, LotSizeConstraint, Verdict
from qsys.core.portfolio import Portfolio
from qsys.core.trading import OrderIntent, Side
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)


def ctx(cash: float = 100_000.0, price: float = 27.58, **kwargs) -> ConstraintContext:
    return ConstraintContext(portfolio=Portfolio(cash), reference_price=price, **kwargs)


def test_verdict_zero_qty_is_rejection():
    assert Verdict(0, "nope").is_rejected is True
    assert Verdict(100).is_rejected is False


def test_lot_size_passes_exact_multiple():
    v = LotSizeConstraint().apply(OrderIntent(CMB, Side.BUY, 200), ctx())
    assert v.allowed_qty == 200
    assert v.reason == ""


def test_lot_size_rounds_buy_down():
    v = LotSizeConstraint().apply(OrderIntent(CMB, Side.BUY, 250), ctx())
    assert v.allowed_qty == 200
    assert "lot" in v.reason


def test_lot_size_rejects_buy_below_one_lot():
    v = LotSizeConstraint().apply(OrderIntent(CMB, Side.BUY, 50), ctx())
    assert v.is_rejected
    assert "below one lot" in v.reason


def test_lot_size_does_not_constrain_sells():
    """A 股卖出可不足整手（零股须一次卖清），故不夹取。"""
    v = LotSizeConstraint().apply(OrderIntent(CMB, Side.SELL, 37), ctx())
    assert v.allowed_qty == 37


def test_lot_size_is_configurable():
    v = LotSizeConstraint(lot=1).apply(OrderIntent(CMB, Side.BUY, 37), ctx())
    assert v.allowed_qty == 37


def test_constraint_has_a_name():
    assert LotSizeConstraint().name == "lot_size"


# --- NoShortConstraint / CashConstraint ---

from datetime import datetime

from qsys.core.constraints import CashConstraint, NoShortConstraint
from qsys.core.trading import Fill

NOW = datetime(2024, 1, 2, 15, 0)


def funded(qty: int, price: float = 10.0, cash: float = 100_000.0) -> Portfolio:
    p = Portfolio(cash)
    if qty:
        p.apply_fill(Fill("o", CMB, Side.BUY, qty, price, 0.0, NOW))
        p.unfreeze()
    return p


def test_no_short_allows_any_buy():
    c = ConstraintContext(portfolio=funded(0), reference_price=10.0)
    assert NoShortConstraint().apply(OrderIntent(CMB, Side.BUY, 500), c).allowed_qty == 500


def test_no_short_rejects_sell_without_position():
    c = ConstraintContext(portfolio=funded(0), reference_price=10.0)
    v = NoShortConstraint().apply(OrderIntent(CMB, Side.SELL, 100), c)
    assert v.is_rejected
    assert "no position" in v.reason


def test_no_short_clamps_sell_to_holding():
    c = ConstraintContext(portfolio=funded(300), reference_price=10.0)
    v = NoShortConstraint().apply(OrderIntent(CMB, Side.SELL, 500), c)
    assert v.allowed_qty == 300
    assert "300" in v.reason


def test_no_short_passes_sell_within_holding():
    c = ConstraintContext(portfolio=funded(300), reference_price=10.0)
    assert NoShortConstraint().apply(OrderIntent(CMB, Side.SELL, 200), c).allowed_qty == 200


def test_cash_allows_any_sell():
    c = ConstraintContext(portfolio=Portfolio(0.0), reference_price=10.0)
    assert CashConstraint().apply(OrderIntent(CMB, Side.SELL, 500), c).allowed_qty == 500


def test_cash_clamps_buy_to_affordable_quantity():
    # 1000 元现金，10 元/股，费用缓冲 0.1% -> 每股 10.01 -> 可买 99 股
    c = ConstraintContext(portfolio=Portfolio(1000.0), reference_price=10.0)
    v = CashConstraint().apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert v.allowed_qty == 99
    assert "cash" in v.reason


def test_cash_rejects_when_nothing_affordable():
    c = ConstraintContext(portfolio=Portfolio(5.0), reference_price=10.0)
    v = CashConstraint().apply(OrderIntent(CMB, Side.BUY, 100), c)
    assert v.is_rejected
    assert "insufficient cash" in v.reason


def test_cash_passes_when_fully_affordable():
    c = ConstraintContext(portfolio=Portfolio(100_000.0), reference_price=10.0)
    assert CashConstraint().apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100


def test_cash_rejects_non_positive_reference_price():
    c = ConstraintContext(portfolio=Portfolio(100_000.0), reference_price=0.0)
    v = CashConstraint().apply(OrderIntent(CMB, Side.BUY, 100), c)
    assert v.is_rejected
    assert "reference price" in v.reason


def test_cash_buffer_is_configurable():
    c = ConstraintContext(portfolio=Portfolio(1000.0), reference_price=10.0)
    assert CashConstraint(fee_buffer=0.0).apply(OrderIntent(CMB, Side.BUY, 500), c).allowed_qty == 100
