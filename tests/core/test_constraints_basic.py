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
