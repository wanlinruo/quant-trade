"""合成场景端到端断言：账户状态精确到分。

这些测试枯燥，但它们是敢用这套内核下真钱的理由。
"""

from datetime import date, datetime

import pytest

from qsys.core.calendar import AShareCalendar
from qsys.core.clock import BacktestClock, Phase
from qsys.core.constraints import ConstraintContext
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderIntent, Side
from qsys.core.types import Market, Symbol
from qsys.markets.ashare import ashare_pipeline

CMB = Symbol("600036", Market.SH)
SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
CAL = AShareCalendar(SESSIONS)
PIPE = ashare_pipeline()


def ctx(p: Portfolio, price: float, prev_close: float | None = None, **kw):
    """测试数据：主板 10% 档下交易所会公布的涨跌停价，作为当日直取值放进上下文。"""
    up = down = None
    if prev_close is not None:
        up, down = round(prev_close * 1.10, 2), round(prev_close * 0.90, 2)
    return ConstraintContext(
        portfolio=p, reference_price=price, limit_up=up, limit_down=down, **kw
    )


def test_t_plus_one_full_cycle():
    """买入 -> 当日不可卖 -> 次日解冻 -> 可卖。"""
    p = Portfolio(100_000.0)

    r = PIPE.apply(OrderIntent(CMB, Side.BUY, 500), ctx(p, 10.00, 9.50))
    assert r.allowed_qty == 500
    p.apply_fill(Fill("o1", CMB, Side.BUY, 500, 10.00, 5.0, datetime(2024, 1, 2, 15, 0)))
    assert p.cash == 100_000.0 - 5000.00 - 5.0
    assert p.position(CMB).total_qty == 500
    assert p.position(CMB).available_qty == 0

    assert PIPE.apply(OrderIntent(CMB, Side.SELL, 500), ctx(p, 10.00, 9.50)).is_rejected

    p.unfreeze()
    assert p.position(CMB).available_qty == 500

    r = PIPE.apply(OrderIntent(CMB, Side.SELL, 500), ctx(p, 11.00, 10.00))
    assert r.allowed_qty == 500
    p.apply_fill(Fill("o2", CMB, Side.SELL, 500, 11.00, 6.0, datetime(2024, 1, 3, 15, 0)))

    assert p.cash == 100_000.0 - 5005.0 + 5500.00 - 6.0
    assert CMB not in p.positions
    assert p.total_value({}) == 100_489.00


def test_limit_up_blocks_buy_but_not_sell():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 500, 10.0, 0.0, datetime(2024, 1, 2, 15, 0)))
    p.unfreeze()

    at_limit = ctx(p, 11.00, 10.00)
    assert PIPE.apply(OrderIntent(CMB, Side.BUY, 100), at_limit).is_rejected
    assert PIPE.apply(OrderIntent(CMB, Side.SELL, 500), at_limit).allowed_qty == 500


def test_limit_down_blocks_sell_but_not_buy():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 500, 10.0, 0.0, datetime(2024, 1, 2, 15, 0)))
    p.unfreeze()

    at_limit = ctx(p, 9.00, 10.00)
    assert PIPE.apply(OrderIntent(CMB, Side.SELL, 500), at_limit).is_rejected
    assert PIPE.apply(OrderIntent(CMB, Side.BUY, 100), at_limit).allowed_qty == 100


def test_suspension_blocks_both_directions():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 500, 10.0, 0.0, datetime(2024, 1, 2, 15, 0)))
    p.unfreeze()

    halted = ctx(p, 10.00, 10.00, is_suspended=True)
    assert PIPE.apply(OrderIntent(CMB, Side.BUY, 100), halted).is_rejected
    assert PIPE.apply(OrderIntent(CMB, Side.SELL, 100), halted).is_rejected


def test_cash_and_lot_size_interact_correctly():
    """3050 元、10 元/股：现金夹到 304 股，整手取整到 300 股。"""
    p = Portfolio(3050.0)
    r = PIPE.apply(OrderIntent(CMB, Side.BUY, 1000), ctx(p, 10.00, 10.00))
    assert r.allowed_qty == 300


def test_partial_sell_keeps_proportional_cost_basis():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o1", CMB, Side.BUY, 1000, 10.00, 0.0, datetime(2024, 1, 2, 15, 0)))
    p.unfreeze()
    p.apply_fill(Fill("o2", CMB, Side.SELL, 400, 12.00, 0.0, datetime(2024, 1, 3, 15, 0)))

    pos = p.position(CMB)
    assert pos.total_qty == 600
    assert pos.cost_basis == 6000.00
    assert pos.avg_cost == 10.00


def test_holding_through_a_split_keeps_market_value():
    """10 送 10：持仓翻倍、单价减半，市值不变。"""
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 1000, 20.00, 0.0, datetime(2024, 1, 2, 15, 0)))
    before = p.market_value({CMB: 20.00})

    p.apply_fill(Fill("split", CMB, Side.BUY, 1000, 0.0, 0.0, datetime(2024, 1, 4, 15, 0)))
    after = p.market_value({CMB: 10.00})

    assert before == after == 20_000.00


def test_clock_drives_a_three_day_session_sequence():
    clock = BacktestClock(CAL, SESSIONS[0], SESSIONS[-1])
    p = Portfolio(100_000.0)
    unfreeze_count = 0

    for _, phase in clock.ticks():
        if phase is Phase.SESSION_START:
            p.unfreeze()
            unfreeze_count += 1

    assert unfreeze_count == 3
    assert clock.now == datetime(2024, 1, 4, 15, 30)


def test_cannot_sell_more_than_held_even_if_constraints_bypassed():
    """记账层的最后一道防线：绕过约束直接记账也必须报错。"""
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 100, 10.0, 0.0, datetime(2024, 1, 2, 15, 0)))
    p.unfreeze()
    with pytest.raises(ValueError, match="cannot sell 200"):
        p.apply_fill(Fill("x", CMB, Side.SELL, 200, 10.0, 0.0, datetime(2024, 1, 3, 15, 0)))


def test_round_trip_pnl_is_exact_to_the_cent():
    """完整一轮买卖后现金余额必须精确到分。"""
    p = Portfolio(50_000.0)
    p.apply_fill(Fill("b", CMB, Side.BUY, 300, 27.583, 5.0, datetime(2024, 1, 2, 15, 0)))
    p.unfreeze()
    p.apply_fill(Fill("s", CMB, Side.SELL, 300, 30.117, 9.03, datetime(2024, 1, 3, 15, 0)))

    assert p.cash == 50_000.0 - 8274.90 - 5.0 + 9035.10 - 9.03
    assert p.cash == 50_746.17
