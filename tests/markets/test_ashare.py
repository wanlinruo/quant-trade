from datetime import datetime

from qsys.core.constraints import ConstraintContext
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderIntent, Side
from qsys.core.types import Market, Symbol
from qsys.markets.ashare import LOT_SIZE, PRICE_LIMITS, ashare_pipeline

CMB = Symbol("600036", Market.SH)
NOW = datetime(2024, 1, 2, 15, 0)


def test_price_limits_cover_every_board_the_data_layer_emits():
    """板块取值必须与计划 1-1 的 _board_of 输出对齐。"""
    assert set(PRICE_LIMITS) == {"main", "gem", "star", "bse", "bshare"}


def test_price_limit_values():
    assert PRICE_LIMITS["main"] == 0.10
    assert PRICE_LIMITS["gem"] == 0.20
    assert PRICE_LIMITS["star"] == 0.20
    assert PRICE_LIMITS["bse"] == 0.30
    assert PRICE_LIMITS["bshare"] == 0.10


def test_lot_size_is_one_hundred():
    assert LOT_SIZE == 100


def test_pipeline_order_puts_lot_size_last():
    """整手必须最后施加，否则现金夹取会留下零股。"""
    assert ashare_pipeline().names == (
        "suspension",
        "price_limit",
        "no_short",
        "settlement",
        "cash",
        "lot_size",
    )


def test_pipeline_enforces_t_plus_one():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 500, 10.0, 0.0, NOW))
    ctx = ConstraintContext(portfolio=p, reference_price=10.0, prev_close=10.0)
    assert ashare_pipeline().apply(OrderIntent(CMB, Side.SELL, 500), ctx).is_rejected


def test_pipeline_enforces_lot_size():
    ctx = ConstraintContext(
        portfolio=Portfolio(100_000.0), reference_price=10.0, prev_close=10.0
    )
    r = ashare_pipeline().apply(OrderIntent(CMB, Side.BUY, 250), ctx)
    assert r.allowed_qty == 200


def test_pipeline_enforces_gem_wider_band():
    ctx = ConstraintContext(
        portfolio=Portfolio(100_000.0), reference_price=11.5, prev_close=10.0, board="gem"
    )
    assert ashare_pipeline().apply(OrderIntent(CMB, Side.BUY, 100), ctx).allowed_qty == 100


def test_fee_buffer_is_configurable():
    ctx = ConstraintContext(
        portfolio=Portfolio(1000.0), reference_price=10.0, prev_close=10.0
    )
    assert ashare_pipeline(fee_buffer=0.0).apply(OrderIntent(CMB, Side.BUY, 500), ctx).allowed_qty == 100


# --- 下单时刻可评估的约束子集 ---

from qsys.core.constraints import ConstraintContext
from qsys.core.portfolio import Portfolio
from qsys.core.trading import OrderIntent
from qsys.markets.ashare import ashare_pre_trade_pipeline


def test_pre_trade_pipeline_excludes_market_dependent_rules():
    """涨跌停与停牌依赖当时行情，核心侧下单时未必持有，留给券商。"""
    names = ashare_pre_trade_pipeline().names
    assert "price_limit" not in names
    assert "suspension" not in names


def test_pre_trade_pipeline_keeps_account_dependent_rules():
    assert ashare_pre_trade_pipeline().names == (
        "no_short", "settlement", "cash", "lot_size",
    )


def test_pre_trade_pipeline_still_puts_lot_size_last():
    assert ashare_pre_trade_pipeline().names[-1] == "lot_size"


def test_pre_trade_pipeline_enforces_t_plus_one():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 500, 10.0, 0.0, NOW))
    ctx = ConstraintContext(portfolio=p, reference_price=10.0)
    assert ashare_pre_trade_pipeline().apply(
        OrderIntent(CMB, Side.SELL, 500), ctx
    ).is_rejected
