from datetime import datetime

import pytest

from qsys.core.constraints import (
    CashConstraint,
    ConstraintContext,
    ConstraintPipeline,
    LotSizeConstraint,
    NoShortConstraint,
    PriceLimitConstraint,
    SettlementConstraint,
    SuspensionConstraint,
)
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderIntent, Side
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)
NOW = datetime(2024, 1, 2, 15, 0)
LIMITS = {"main": 0.10}

PIPELINE = ConstraintPipeline(
    [
        SuspensionConstraint(),
        PriceLimitConstraint(LIMITS),
        NoShortConstraint(),
        SettlementConstraint(),
        CashConstraint(),
        LotSizeConstraint(),
    ]
)


def held(total: int, settled: bool, cash: float) -> Portfolio:
    p = Portfolio(cash)
    if total:
        p.apply_fill(Fill("o", CMB, Side.BUY, total, 10.0, 0.0, NOW))
        if settled:
            p.unfreeze()
    return p


def test_clean_buy_passes_through():
    c = ConstraintContext(portfolio=held(0, False, 100_000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert r.allowed_qty == 500
    assert r.is_rejected is False


def test_cash_then_lot_yields_whole_lots():
    """现金只够 99 股 -> 整手取整后为 0 -> 拒绝。整手必须排在现金之后。"""
    c = ConstraintContext(portfolio=held(0, False, 1000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert r.is_rejected
    assert r.binding_reason.startswith("lot_size")


def test_cash_then_lot_yields_two_lots():
    c = ConstraintContext(portfolio=held(0, False, 3000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert r.allowed_qty == 200


def test_first_rejection_stops_the_pipeline():
    c = ConstraintContext(portfolio=held(0, False, 100_000.0), reference_price=10.0,
                          prev_close=10.0, is_suspended=True)
    r = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert r.is_rejected
    assert r.binding_reason.startswith("suspension")
    assert [name for name, _ in r.checks] == ["suspension"]


def test_checks_record_every_constraint_that_ran():
    c = ConstraintContext(portfolio=held(0, False, 100_000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert [name for name, _ in r.checks] == list(PIPELINE.names)


def test_binding_reason_names_the_tightest_constraint():
    c = ConstraintContext(portfolio=held(500, True, 100_000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.SELL, 900), c)
    assert r.allowed_qty == 500
    assert r.binding_reason.startswith("no_short")


def test_settlement_blocks_same_day_sell():
    c = ConstraintContext(portfolio=held(500, False, 100_000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.SELL, 500), c)
    assert r.is_rejected
    assert r.binding_reason.startswith("settlement")


def test_no_binding_reason_when_nothing_clamped():
    c = ConstraintContext(portfolio=held(0, False, 100_000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert r.binding_reason == ""


def test_as_log_fields_is_flat_and_serializable():
    c = ConstraintContext(portfolio=held(0, False, 3000.0), reference_price=10.0,
                          prev_close=10.0)
    fields = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c).as_log_fields()
    assert fields["allowed_qty"] == 200
    assert isinstance(fields["checks"], str)
    assert "cash" in fields["checks"]


def test_empty_pipeline_passes_everything():
    c = ConstraintContext(portfolio=held(0, False, 0.0), reference_price=10.0)
    r = ConstraintPipeline([]).apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert r.allowed_qty == 500
    assert r.checks == ()


def test_constraint_cannot_increase_quantity():
    """流水线必须防御性地拒绝放大数量的约束实现。"""

    class BadConstraint(SuspensionConstraint):
        name = "bad"

        def apply(self, intent, ctx):
            from qsys.core.constraints import Verdict

            return Verdict(intent.qty * 10)

    c = ConstraintContext(portfolio=held(0, False, 100_000.0), reference_price=10.0)
    with pytest.raises(ValueError, match="cannot increase"):
        ConstraintPipeline([BadConstraint()]).apply(OrderIntent(CMB, Side.BUY, 100), c)
