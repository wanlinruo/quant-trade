"""策略组装：选股下单 + 三个检查点退出。"""

from datetime import date, datetime, time

import pytest

from qsys.core.trading import Fill, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.strategy.structural.strategy import AUCTION_AT, CHECKPOINTS, StructuralStrategy

SYM = Symbol("600001", Market.SH)
SYM2 = Symbol("000002", Market.SZ)
DAY = date(2024, 1, 4)


class FakeSelection:
    def __init__(self, picks=(), funnel=None):
        self.picks = list(picks)
        self.calls = []
        self.funnel = funnel

    def select(self, day, previous):
        self.calls.append((day, previous))
        return self.picks


class Pick:
    def __init__(self, symbol, tag):
        self.symbol, self.tag = symbol, tag


class FakeOrder:
    def __init__(self):
        self.submitted = []
        self.styles = []   # 每单的 (order_type, limit_price)，只在关心时断言

    def submit(self, symbol, side, qty, order_type=None, limit_price=None):
        self.submitted.append((str(symbol), side, qty))
        self.styles.append((order_type, limit_price))


class FakePosition:
    def __init__(self, qty=0, available=0, avg=0.0):
        self.total_qty, self.available_qty, self.avg_cost = qty, available, avg


class FakePortfolio:
    def __init__(self, cash=100_000.0, positions=None):
        self.cash = cash
        self._p = positions or {}

    def position(self, symbol):
        return self._p.get(str(symbol), FakePosition())

    @property
    def symbols(self):
        return frozenset(self._p)


class FakeMarket:
    def __init__(self, opens=None, intraday=None, limit_up=None):
        self._opens = opens or {}
        self._intraday = intraday or {}
        self._limit_up = limit_up or {}

    def session_open(self, symbols, day):
        return {s: v for s, v in self._opens.items() if s in symbols}

    def intraday(self, symbols, day, at):
        return {s: v for s, v in self._intraday.items() if s in symbols}

    def limit_up_price(self, symbols, day):
        return {s: v for s, v in self._limit_up.items() if s in symbols}


class Quote:
    def __init__(self, price, volume=1000):
        self.price, self.volume = price, volume


class FakeData:
    def __init__(self, closes=()):
        self._closes = list(closes)

    def get(self, symbols, start, end, adjustment=None):
        import polars as pl

        n = len(self._closes)
        return pl.DataFrame(
            {
                "symbol": [str(SYM)] * n,
                "dt": [date(2024, 1, 1)] * n,
                "open": self._closes, "high": self._closes,
                "low": self._closes, "close": self._closes,
                "volume": [1000] * n, "amount": [0.0] * n,
            }
        )


class FakeCtx:
    def __init__(
        self, now, portfolio=None, market=None, data=None, state=None, report=None
    ):
        self.now = now
        self.portfolio = portfolio or FakePortfolio()
        self.market = market or FakeMarket()
        self.data = data or FakeData()
        self.order = FakeOrder()
        self.state = state if state is not None else {}
        self.params = {}
        self._report = report
        import structlog

        self.log = structlog.get_logger("test")

    def report(self, **kwargs):
        if self._report is not None:
            self._report.report(**kwargs)


# ---------------- 买入 ----------------


def test_without_a_calendar_the_first_session_places_no_orders():
    """状态里没攒到上一交易日、又没有日历，就无从选股。"""
    strategy = StructuralStrategy(FakeSelection([Pick(str(SYM), "LOW_MAIN")]))
    ctx = FakeCtx(datetime.combine(DAY, time(9, 15)))
    strategy.on_session_start(ctx)
    strategy.on_auction(ctx)
    assert ctx.order.submitted == []


def test_the_first_session_uses_the_calendar_for_the_previous_session():
    """新账户第一天、状态重置、回测区间首日都不该白白空转一天。"""
    from qsys.core.calendar import AShareCalendar

    sel = FakeSelection([Pick(str(SYM), "LOW_MAIN")])
    calendar = AShareCalendar([date(2024, 1, 2), DAY])
    strategy = StructuralStrategy(sel, calendar=calendar)
    ctx = FakeCtx(datetime.combine(DAY, AUCTION_AT),
                  market=FakeMarket(opens={str(SYM): 10.0}))

    strategy.on_session_start(ctx)
    strategy.on_auction(ctx)

    assert sel.calls == [(DAY, date(2024, 1, 2))]          # 用日历查到的 T-1
    assert ctx.order.submitted == [(str(SYM), Side.BUY, 9900)]


def test_state_wins_over_the_calendar_when_it_has_one():
    """攒到的上一交易日优先：回测/实盘连续运行时口径不变。"""
    from qsys.core.calendar import AShareCalendar

    sel = FakeSelection([Pick(str(SYM), "LOW_MAIN")])
    strategy = StructuralStrategy(sel, calendar=AShareCalendar([date(2024, 1, 2), DAY]))
    state = {"previous_session": date(2023, 12, 29), "last_session": date(2023, 12, 29)}
    ctx = FakeCtx(datetime.combine(DAY, AUCTION_AT),
                  market=FakeMarket(opens={str(SYM): 10.0}), state=state)

    strategy.on_auction(ctx)

    assert sel.calls == [(DAY, date(2023, 12, 29))]


def test_auction_reports_the_funnel():
    from qsys.ops.strategy_log import RunReport
    from qsys.strategy.structural.selection import SelectionFunnel

    report = RunReport()
    funnel = SelectionFunnel(
        pool=37, routed=30, allowed=1, reasons={"高开超过阈值": 12}
    )
    selection = FakeSelection([Pick(str(SYM), "LOW_MAIN")], funnel=funnel)
    strategy = StructuralStrategy(selection)
    state = {"previous_session": date(2024, 1, 3)}
    ctx = FakeCtx(
        datetime.combine(DAY, AUCTION_AT),
        market=FakeMarket(opens={str(SYM): 10.0}),
        state=state,
        report=report,
    )

    strategy.on_auction(ctx)

    assert report.candidates == 37 and report.passed == 1
    assert report.reasons["闸门·高开超过阈值"] == 12


def test_auction_without_a_previous_session_says_so():
    from qsys.ops.strategy_log import RunReport

    report = RunReport()
    strategy = StructuralStrategy(FakeSelection([Pick(str(SYM), "LOW_MAIN")]))
    ctx = FakeCtx(datetime.combine(DAY, time(9, 15)), report=report)

    strategy.on_auction(ctx)

    assert report.note == "没有上一交易日，无从选股"


def test_a_pick_becomes_a_buy_order():
    sel = FakeSelection([Pick(str(SYM), "LOW_MAIN")])
    strategy = StructuralStrategy(sel)
    state = {}
    strategy.on_session_start(FakeCtx(datetime.combine(date(2024, 1, 3), time(9, 15)), state=state))
    ctx = FakeCtx(
        datetime.combine(DAY, AUCTION_AT),
        market=FakeMarket(opens={str(SYM): 10.0}),
        state=state,
    )
    strategy.on_session_start(ctx)
    strategy.on_auction(ctx)
    # CASH-SAFE V2：按 10.00 x 1.002 定量并预留费用，故 9,900 股而非 10,000
    assert ctx.order.submitted == [(str(SYM), Side.BUY, 9900)]
    # 并且是挂在保护价上的限价单，不是市价单
    assert ctx.order.styles == [(OrderType.LIMIT, pytest.approx(10.02))]


def test_entry_price_buffer_param_lifts_the_protect_price():
    """09:25 撮合后才下单时，保护价上浮可配置；定量也按新保护价算。"""
    sel = FakeSelection([Pick(str(SYM), "LOW_MAIN")])
    strategy = StructuralStrategy(sel)
    state = {}
    strategy.on_session_start(FakeCtx(datetime.combine(date(2024, 1, 3), time(9, 15)), state=state))
    ctx = FakeCtx(
        datetime.combine(DAY, AUCTION_AT),
        market=FakeMarket(opens={str(SYM): 10.0}),
        state=state,
    )
    ctx.params = {"entry_price_buffer": 0.01}
    strategy.on_session_start(ctx)
    strategy.on_auction(ctx)
    assert ctx.order.styles == [(OrderType.LIMIT, pytest.approx(10.10))]
    assert ctx.order.submitted == [(str(SYM), Side.BUY, 9800)]


def test_the_tag_is_remembered_for_the_exit_side():
    """退出要按分支分流，买入时就得记下标签。"""
    sel = FakeSelection([Pick(str(SYM), "HIGH_MAIN")])
    strategy = StructuralStrategy(sel)
    state = {}
    strategy.on_session_start(FakeCtx(datetime.combine(date(2024, 1, 3), time(9, 15)), state=state))
    c = FakeCtx(datetime.combine(DAY, AUCTION_AT),
                market=FakeMarket(opens={str(SYM): 10.0}), state=state)
    strategy.on_session_start(c)
    strategy.on_auction(c)
    assert state["tags"][str(SYM)] == "HIGH_MAIN"


def test_real_buy_fill_creates_a_persistent_lot_and_sell_reduces_it():
    strategy = StructuralStrategy(FakeSelection())
    state = {"tags": {str(SYM): "LOW_MAIN"}}
    ctx = FakeCtx(datetime.combine(DAY, time(10)), state=state)
    strategy.on_fill(ctx, Fill("b1", SYM, Side.BUY, 500, 10.2, 5.0,
                               datetime.combine(DAY, time(10))))
    assert state["lots"][str(SYM)][0]["entry_price"] == 10.2
    strategy.on_fill(ctx, Fill("s1", SYM, Side.SELL, 200, 11.0, 5.0,
                               datetime.combine(DAY, time(14))))
    assert state["lots"][str(SYM)][0]["qty"] == 300


def test_checkpoint_orders_held_symbols_deterministically():
    positions = {
        str(SYM): FakePosition(100, 100, 10.0),
        str(SYM2): FakePosition(200, 200, 10.0),
    }
    state = {
        "tags": {str(SYM): "LOW_MAIN", str(SYM2): "LOW_MAIN"},
        "lots": {
            str(SYM): [{"qty": 100, "entry_price": 10.0, "tag": "LOW_MAIN",
                        "acquired_on": "2024-01-03"}],
            str(SYM2): [{"qty": 200, "entry_price": 10.0, "tag": "LOW_MAIN",
                         "acquired_on": "2024-01-03"}],
        },
    }
    quotes = {str(SYM): Quote(10.2), str(SYM2): Quote(10.2)}
    ctx = FakeCtx(
        datetime.combine(DAY, time(11, 25)),
        portfolio=FakePortfolio(positions=positions),
        market=FakeMarket(intraday=quotes), state=state,
    )

    StructuralStrategy(FakeSelection()).on_checkpoint(ctx, time(11, 25))

    assert [row[0] for row in ctx.order.submitted] == [str(SYM2), str(SYM)]


def test_exit_is_evaluated_per_lot_entry_price():
    portfolio = FakePortfolio(positions={str(SYM): FakePosition(1000, 1000, 10.5)})
    state = {
        "tags": {str(SYM): "HIGH_MAIN"},
        "lots": {str(SYM): [
            {"qty": 400, "entry_price": 9.0, "tag": "HIGH_MAIN", "acquired_on": "2024-01-03"},
            {"qty": 600, "entry_price": 12.0, "tag": "HIGH_MAIN", "acquired_on": "2024-01-03"},
        ]},
    }
    ctx = FakeCtx(
        datetime.combine(DAY, time(11, 25)), portfolio=portfolio, state=state,
        market=FakeMarket(intraday={str(SYM): Quote(10.0)}, limit_up={str(SYM): 12.0}),
        data=FakeData([10.0] * 4),
    )
    StructuralStrategy(FakeSelection()).on_checkpoint(ctx, time(11, 25))
    assert ctx.order.submitted == [(str(SYM), Side.SELL, 400)]


def test_no_open_price_means_no_order():
    sel = FakeSelection([Pick(str(SYM), "LOW_MAIN")])
    strategy = StructuralStrategy(sel)
    state = {}
    strategy.on_session_start(FakeCtx(datetime.combine(date(2024, 1, 3), time(9, 15)), state=state))
    ctx = FakeCtx(datetime.combine(DAY, AUCTION_AT), state=state)
    strategy.on_session_start(ctx)
    strategy.on_auction(ctx)
    assert ctx.order.submitted == []


# ---------------- 退出 ----------------


def held(tag, qty=1000, avg=10.0):
    return {
        "portfolio": FakePortfolio(
            positions={str(SYM): FakePosition(qty, qty, avg)}
        ),
        "state": {"tags": {str(SYM): tag}},
    }


def test_high_takes_profit_at_1125():
    h = held("HIGH_MAIN")
    ctx = FakeCtx(
        datetime.combine(DAY, time(11, 25)), portfolio=h["portfolio"], state=h["state"],
        market=FakeMarket(intraday={str(SYM): Quote(11.0)}, limit_up={str(SYM): 12.0}),
        data=FakeData([10.0, 10.0, 10.0, 10.0]),
    )
    StructuralStrategy(FakeSelection()).on_checkpoint(ctx, time(11, 25))
    assert ctx.order.submitted == [(str(SYM), Side.SELL, 1000)]


def test_low_holds_at_1125_when_not_profitable():
    h = held("LOW_MAIN")
    ctx = FakeCtx(
        datetime.combine(DAY, time(11, 25)), portfolio=h["portfolio"], state=h["state"],
        market=FakeMarket(intraday={str(SYM): Quote(9.5)}, limit_up={str(SYM): 12.0}),
    )
    StructuralStrategy(FakeSelection()).on_checkpoint(ctx, time(11, 25))
    assert ctx.order.submitted == []


def test_low_sells_unconditionally_at_1425():
    h = held("LOW_MAIN")
    ctx = FakeCtx(
        datetime.combine(DAY, time(14, 25)), portfolio=h["portfolio"], state=h["state"],
        market=FakeMarket(intraday={str(SYM): Quote(9.0)}, limit_up={str(SYM): 12.0}),
    )
    StructuralStrategy(FakeSelection()).on_checkpoint(ctx, time(14, 25))
    assert ctx.order.submitted == [(str(SYM), Side.SELL, 1000)]


def test_high_is_untouched_at_1425():
    """14:25 只作用于 LOW。"""
    h = held("HIGH_MAIN")
    ctx = FakeCtx(
        datetime.combine(DAY, time(14, 25)), portfolio=h["portfolio"], state=h["state"],
        market=FakeMarket(intraday={str(SYM): Quote(9.0)}, limit_up={str(SYM): 12.0}),
        data=FakeData([10.0] * 4),
    )
    StructuralStrategy(FakeSelection()).on_checkpoint(ctx, time(14, 25))
    assert ctx.order.submitted == []


def test_low_is_untouched_at_1450():
    """14:50 只作用于 HIGH。"""
    h = held("LOW_MAIN")
    ctx = FakeCtx(
        datetime.combine(DAY, time(14, 50)), portfolio=h["portfolio"], state=h["state"],
        market=FakeMarket(intraday={str(SYM): Quote(9.0)}, limit_up={str(SYM): 12.0}),
    )
    StructuralStrategy(FakeSelection()).on_checkpoint(ctx, time(14, 50))
    assert ctx.order.submitted == []


def test_limit_up_holds_overnight():
    h = held("LOW_MAIN")
    ctx = FakeCtx(
        datetime.combine(DAY, time(14, 25)), portfolio=h["portfolio"], state=h["state"],
        market=FakeMarket(intraday={str(SYM): Quote(12.0)}, limit_up={str(SYM): 12.0}),
    )
    StructuralStrategy(FakeSelection()).on_checkpoint(ctx, time(14, 25))
    assert ctx.order.submitted == []


def test_only_settled_shares_are_sold():
    """当日买入不可卖——只卖已解冻的部分。"""
    portfolio = FakePortfolio(positions={str(SYM): FakePosition(2000, 600, 10.0)})
    ctx = FakeCtx(
        datetime.combine(DAY, time(14, 25)), portfolio=portfolio,
        state={"tags": {str(SYM): "LOW_MAIN"}},
        market=FakeMarket(intraday={str(SYM): Quote(9.0)}, limit_up={str(SYM): 12.0}),
    )
    StructuralStrategy(FakeSelection()).on_checkpoint(ctx, time(14, 25))
    assert ctx.order.submitted == [(str(SYM), Side.SELL, 600)]


def test_no_intraday_quote_means_no_exit():
    """拿不到当时价格就不能判——绝不用别的价格顶替。"""
    h = held("LOW_MAIN")
    ctx = FakeCtx(
        datetime.combine(DAY, time(14, 25)), portfolio=h["portfolio"], state=h["state"],
        market=FakeMarket(limit_up={str(SYM): 12.0}),
    )
    StructuralStrategy(FakeSelection()).on_checkpoint(ctx, time(14, 25))
    assert ctx.order.submitted == []


def test_an_untagged_position_is_left_alone():
    """没有标签就不知道该按哪条规则退，宁可不动。"""
    ctx = FakeCtx(
        datetime.combine(DAY, time(14, 25)),
        portfolio=FakePortfolio(positions={str(SYM): FakePosition(1000, 1000, 10.0)}),
        state={},
        market=FakeMarket(intraday={str(SYM): Quote(9.0)}, limit_up={str(SYM): 12.0}),
    )
    StructuralStrategy(FakeSelection()).on_checkpoint(ctx, time(14, 25))
    assert ctx.order.submitted == []


def test_checkpoints_are_the_three_from_the_original():
    assert CHECKPOINTS == (time(11, 25), time(14, 25), time(14, 50))


def test_buying_happens_at_the_auction_not_at_0915():
    """09:15 竞价还没结算，开盘价不可见——买入必须在竞价时刻。"""
    assert AUCTION_AT == time(9, 30)


def test_auction_time_param_moves_the_decision_after_the_auction_match():
    from qsys.strategy.schedule import Schedule
    from qsys.strategy.structural.strategy import auction_time

    ctx = FakeCtx(datetime.combine(DAY, time(9, 0)))
    ctx.schedule = Schedule()
    StructuralStrategy(FakeSelection()).on_init(ctx)
    assert ctx.schedule.auction_at == time(9, 30)       # 默认仍是原策略

    ctx.schedule = Schedule()
    ctx.params = {"auction_at": "09:25:10"}
    StructuralStrategy(FakeSelection()).on_init(ctx)
    assert ctx.schedule.auction_at == time(9, 25, 10)

    assert auction_time("09:30") == time(9, 30)
    with pytest.raises(ValueError):
        auction_time("09:24:50")                        # 撮合结果还不可见


def test_sizing_buffer_is_separate_from_the_protect_price():
    """保护价 +3% 保证成交，定量只按 +0.5% 预留，不必少买 3%。"""
    sel = FakeSelection([Pick(str(SYM), "LOW_MAIN")])
    strategy = StructuralStrategy(sel)
    state = {}
    strategy.on_session_start(FakeCtx(datetime.combine(date(2024, 1, 3), time(9, 15)), state=state))
    ctx = FakeCtx(
        datetime.combine(DAY, AUCTION_AT),
        market=FakeMarket(opens={str(SYM): 10.0}),
        state=state,
    )
    ctx.params = {"entry_price_buffer": 0.03, "sizing_price_buffer": 0.005}
    strategy.on_session_start(ctx)
    strategy.on_auction(ctx)
    assert ctx.order.styles == [(OrderType.LIMIT, pytest.approx(10.30))]
    # 按 10.05 定量：(100000 - 预留) / 10.05 向下取整到手
    assert ctx.order.submitted == [(str(SYM), Side.BUY, 9900)]


def test_the_runtime_calendar_reaches_a_snapshot_strategy():
    """模拟账户跑的是创建时存档的源码，构造参数改不到它；日历要能从 ctx 注入。"""
    from qsys.core.calendar import AShareCalendar

    sel = FakeSelection([Pick(str(SYM), "LOW_MAIN")])
    strategy = StructuralStrategy(sel)            # 没有构造期日历
    ctx = FakeCtx(datetime.combine(DAY, AUCTION_AT),
                  market=FakeMarket(opens={str(SYM): 10.0}))
    ctx.calendar = AShareCalendar([date(2024, 1, 2), DAY])

    strategy.on_auction(ctx)

    assert sel.calls == [(DAY, date(2024, 1, 2))]
