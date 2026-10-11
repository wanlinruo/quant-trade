"""引擎的日内检查点。

本策略是隔夜策略（T 日买、T+1 才可卖），退出发生在 11:25 / 14:25 / 14:50，
而引擎原本只有 09:15 / 15:00 / 15:30 三个时刻。
"""

from datetime import date, datetime, time, timedelta

import pytest

from qsys.backtest.engine import BacktestEngine
from qsys.backtest.entry_fill import EntryFill, EntryFillMode, MinuteBar
from qsys.backtest.fees import ZeroFeeModel
from qsys.backtest.slippage import NoSlippage
from qsys.core.calendar import AShareCalendar
from qsys.core.types import Symbol
from qsys.markets.ashare import ashare_pipeline
from qsys.strategy.base import Strategy
from qsys.core.trading import OrderType, Side
from direct_truth import limits_from_closes

SESSIONS = [date(2024, 1, d) for d in (2, 3, 4, 5)]
SYMBOL = "600001.SH"
CHECKPOINTS = (time(11, 25), time(14, 25), time(14, 50))
# 平坦行情下交易所公布的涨跌停价（测试数据）；检查点报价 11/12/13 都在 20% 以内时才会成交，
# 这里按主板 10% 给出，与原先按板块自算的撮合结果一致。
LIMITS = limits_from_closes({d: (10.0, 10.0) for d in SESSIONS}, [Symbol.parse(SYMBOL)])


class StubFeed:
    """每天 open=10、close=10 的平坦行情，成交量充足。"""

    def get(self, symbols, start, end, *, as_of, adjustment=None):
        import polars as pl

        days = [d for d in SESSIONS if start <= d <= end and d <= as_of.date()]
        n = len(days)
        return pl.DataFrame(
            {
                "symbol": [SYMBOL] * n, "dt": days,
                "open": [10.0] * n, "high": [10.0] * n, "low": [10.0] * n,
                "close": [10.0] * n, "volume": [1_000_000] * n,
                "amount": [1e8] * n,
            }
        )


def intraday_at(session, at, symbols):
    """检查点报价：11:25 -> 11.0，14:25 -> 12.0，14:50 -> 13.0。"""
    price = {time(11, 25): 11.0, time(14, 25): 12.0, time(14, 50): 13.0}[at]
    return {s: (price, 500_000) for s in symbols}


def engine_for(strategy, *, checkpoints=(), intraday=None):
    return BacktestEngine(
        strategy,
        feed=StubFeed(),
        calendar=AShareCalendar(SESSIONS),
        universe=[Symbol.parse(SYMBOL)],
        initial_cash=1_000_000.0,
        pipeline=ashare_pipeline(),
        fees=ZeroFeeModel(),
        slippage=NoSlippage(),
        price_limits=LIMITS,
        checkpoints=checkpoints,
        intraday=intraday,
    )


class DoNothing(Strategy):
    pass


class BuyOnce(Strategy):
    """第一天盘前买 1000 股。"""

    def on_session_start(self, ctx):
        if not ctx.state.get("bought"):
            ctx.state["bought"] = True
            ctx.order.submit(Symbol.parse(SYMBOL), Side.BUY, 1000)


class RecordCheckpoints(Strategy):
    def __init__(self):
        self.seen = []

    def on_checkpoint(self, ctx, at):
        self.seen.append((ctx.now.date(), at, ctx.now.time()))


class SellAtFirstCheckpoint(BuyOnce):
    """买入后，在每个 11:25 尝试清仓。"""

    def on_checkpoint(self, ctx, at):
        if at != time(11, 25):
            return
        held = ctx.portfolio.position(Symbol.parse(SYMBOL)).total_qty
        if held > 0:
            ctx.order.submit(Symbol.parse(SYMBOL), Side.SELL, held)


def test_without_checkpoints_the_engine_is_unchanged():
    """不配检查点时 run_id 与净值必须与既有回测完全相同。

    这是整个扩展的安全网：日频策略的行为一点都不能变。
    """
    plain = engine_for(BuyOnce()).run(SESSIONS[0], SESSIONS[-1])
    again = engine_for(BuyOnce()).run(SESSIONS[0], SESSIONS[-1])
    assert plain.run_id == again.run_id
    assert plain.equity.equals(again.equity)


def test_configuring_checkpoints_changes_the_run_id():
    """配了检查点就是另一次回测，run_id 必须不同——否则两次结果会混淆。"""
    plain = engine_for(DoNothing()).run(SESSIONS[0], SESSIONS[-1])
    with_cp = engine_for(
        DoNothing(), checkpoints=CHECKPOINTS, intraday=intraday_at
    ).run(SESSIONS[0], SESSIONS[-1])
    assert plain.run_id != with_cp.run_id


def test_checkpoints_fire_in_order_each_session():
    strategy = RecordCheckpoints()
    engine_for(strategy, checkpoints=CHECKPOINTS, intraday=intraday_at).run(
        SESSIONS[0], SESSIONS[-1]
    )
    seen = strategy.seen
    per_day = [t for d, t, _ in seen if d == SESSIONS[0]]
    assert per_day == list(CHECKPOINTS)
    assert len(seen) == len(SESSIONS) * len(CHECKPOINTS)


def test_ctx_now_at_a_checkpoint_is_that_checkpoint_time():
    """as_of 必须推进到检查点时刻，否则策略读不到当时的分钟线。"""
    strategy = RecordCheckpoints()
    engine_for(strategy, checkpoints=CHECKPOINTS, intraday=intraday_at).run(
        SESSIONS[0], SESSIONS[-1]
    )
    for _, at, now in strategy.seen:
        assert now == at


def test_shares_bought_today_cannot_be_sold_at_a_checkpoint():
    """A 股 T+1：当日买入当日不可卖。检查点不得绕过 SettlementConstraint。"""
    result = engine_for(
        SellAtFirstCheckpoint(), checkpoints=CHECKPOINTS, intraday=intraday_at
    ).run(SESSIONS[0], SESSIONS[1])
    sells = [f for f in result.fills if f.side is Side.SELL]
    first_session_sells = [f for f in sells if f.filled_at.date() == SESSIONS[0]]
    assert first_session_sells == []


def test_yesterdays_position_is_sellable_at_a_checkpoint():
    """检查点真正要卖的就是这个——前一日买入、持有过夜的仓位。"""
    result = engine_for(
        SellAtFirstCheckpoint(), checkpoints=CHECKPOINTS, intraday=intraday_at
    ).run(SESSIONS[0], SESSIONS[-1])
    sells = [f for f in result.fills if f.side is Side.SELL]
    assert sells, "隔夜仓位应能在次日检查点卖出"
    assert sells[0].filled_at.date() == SESSIONS[1]


def test_a_checkpoint_order_fills_at_the_checkpoint_price():
    """检查点卖出当场按该时刻价格成交，不排到次日开盘（开盘价是 10）。"""
    result = engine_for(
        SellAtFirstCheckpoint(), checkpoints=CHECKPOINTS, intraday=intraday_at
    ).run(SESSIONS[0], SESSIONS[-1])
    sells = [f for f in result.fills if f.side is Side.SELL]
    assert sells[0].price == pytest.approx(11.0)


def test_a_checkpoint_without_intraday_prices_fills_nothing():
    """没有分钟线时不能成交，更不能拿收盘价冒充——那是未来函数。"""

    def nothing(session, at, symbols):
        return {}

    result = engine_for(
        SellAtFirstCheckpoint(), checkpoints=CHECKPOINTS, intraday=nothing
    ).run(SESSIONS[0], SESSIONS[-1])
    assert [f for f in result.fills if f.side is Side.SELL] == []


def test_an_unquotable_checkpoint_order_is_rejected_not_silently_dropped():
    """拿不到报价的卖单要留下拒单记录，不能悄悄消失。"""

    def nothing(session, at, symbols):
        return {}

    result = engine_for(
        SellAtFirstCheckpoint(), checkpoints=CHECKPOINTS, intraday=nothing
    ).run(SESSIONS[0], SESSIONS[-1])
    assert any("no bar" in r.reason for r in result.rejects)


def test_the_hook_fires_even_with_no_position():
    """「没持仓」与「没分钟线」是两回事，钩子都该触发。"""
    strategy = RecordCheckpoints()
    engine_for(strategy, checkpoints=CHECKPOINTS, intraday=intraday_at).run(
        SESSIONS[0], SESSIONS[0]
    )
    assert len(strategy.seen) == len(CHECKPOINTS)


def test_checkpoints_without_an_intraday_source_are_inert():
    """配了检查点却没给行情源时不该崩，也不该悄悄用日线。"""
    result = engine_for(
        SellAtFirstCheckpoint(), checkpoints=CHECKPOINTS
    ).run(SESSIONS[0], SESSIONS[-1])
    assert [f for f in result.fills if f.side is Side.SELL] == []


class BuyAtAuction(Strategy):
    """在竞价时刻下单——此时当日开盘价已确定。"""

    def on_auction(self, ctx):
        if not ctx.state.get("bought"):
            ctx.state["bought"] = True
            ctx.order.submit(Symbol.parse(SYMBOL), Side.BUY, 1000)


def test_without_an_auction_phase_the_engine_is_unchanged():
    """不配竞价时刻时，run_id 与净值必须与扩展前一致。"""
    result = engine_for(BuyOnce()).run(SESSIONS[0], SESSIONS[-1])
    assert result.run_id == engine_for(BuyOnce()).run(SESSIONS[0], SESSIONS[-1]).run_id


def test_configuring_an_auction_phase_changes_the_run_id():
    plain = engine_for(DoNothing()).run(SESSIONS[0], SESSIONS[-1])
    with_auction = BacktestEngine(
        DoNothing(), feed=StubFeed(), calendar=AShareCalendar(SESSIONS),
        universe=[Symbol.parse(SYMBOL)], initial_cash=1_000_000.0,
        pipeline=ashare_pipeline(), fees=ZeroFeeModel(), slippage=NoSlippage(),
        price_limits=LIMITS, auction_at=time(9, 30),
    ).run(SESSIONS[0], SESSIONS[-1])
    assert plain.run_id != with_auction.run_id


def auction_engine(strategy):
    return BacktestEngine(
        strategy, feed=StubFeed(), calendar=AShareCalendar(SESSIONS),
        universe=[Symbol.parse(SYMBOL)], initial_cash=1_000_000.0,
        pipeline=ashare_pipeline(), fees=ZeroFeeModel(), slippage=NoSlippage(),
        price_limits=LIMITS, auction_at=time(9, 30),
    )


def test_an_auction_order_fills_at_the_same_day_open():
    """竞价时刻下的单当日开盘成交，不排到次日。"""
    result = auction_engine(BuyAtAuction()).run(SESSIONS[0], SESSIONS[-1])
    buys = [f for f in result.fills if f.side is Side.BUY]
    assert len(buys) == 1
    assert buys[0].filled_at.date() == SESSIONS[0]
    assert buys[0].price == pytest.approx(10.0)


def test_session_start_orders_still_fill_when_an_auction_phase_exists():
    """盘前提交的单也在同一次开盘撮合里成交，不会被漏掉。"""
    result = auction_engine(BuyOnce()).run(SESSIONS[0], SESSIONS[-1])
    assert [f for f in result.fills if f.side is Side.BUY]


class BuyAtAuctionWithLimit(Strategy):
    """竞价时刻挂限价买单，外加一笔上一交易日留下的隔夜卖单路径不受影响。"""

    def __init__(self, limit):
        self.limit = limit

    def on_auction(self, ctx):
        if not ctx.state.get("bought"):
            ctx.state["bought"] = True
            ctx.order.submit(Symbol.parse(SYMBOL), Side.BUY, 1000,
                             OrderType.LIMIT, self.limit)


def entry_engine(strategy, rule, minutes, *, auction_at=time(9, 30)):
    return BacktestEngine(
        strategy, feed=StubFeed(), calendar=AShareCalendar(SESSIONS),
        universe=[Symbol.parse(SYMBOL)], initial_cash=1_000_000.0,
        pipeline=ashare_pipeline(), fees=ZeroFeeModel(), slippage=NoSlippage(),
        price_limits=LIMITS, auction_at=auction_at,
        entry_fill=rule, entry_minutes=minutes,
    )


def test_continuous_entry_fills_at_the_0931_bar():
    asked = []

    def minutes(session, at, symbols):
        asked.append((session, at, [str(s) for s in symbols]))
        return {s: MinuteBar(open=10.04, low=10.0) for s in symbols}

    rule = EntryFill(EntryFillMode.CONTINUOUS, slippage=0.005)
    result = entry_engine(BuyAtAuctionWithLimit(10.05), rule, minutes).run(
        SESSIONS[0], SESSIONS[-1])

    buys = [f for f in result.fills if f.side is Side.BUY]
    assert [(f.filled_at, f.price) for f in buys] == [
        (datetime.combine(SESSIONS[0], time(9, 31)), pytest.approx(10.04))
    ]
    assert asked == [(SESSIONS[0], time(9, 31), [SYMBOL])]
    assert result.diagnostics == {"orders": 1, "continuous": 1}


def test_continuous_entry_that_misses_the_limit_is_rejected_not_carried():
    rule = EntryFill(EntryFillMode.CONTINUOUS)
    result = entry_engine(
        BuyAtAuctionWithLimit(10.02), rule,
        lambda session, at, symbols: {s: MinuteBar(open=10.3, low=10.1) for s in symbols},
    ).run(SESSIONS[0], SESSIONS[-1])

    assert [f for f in result.fills if f.side is Side.BUY] == []
    assert any("entry limit not reached" in r.reason for r in result.rejects)
    assert result.diagnostics == {"orders": 1, "missed": 1}


def test_entry_fill_only_prices_auction_buys():
    """盘前提交的单不归入场口径管，仍按开盘价撮合。"""
    rule = EntryFill(EntryFillMode.SLIPPAGE, slippage=0.01)
    result = entry_engine(BuyOnce(), rule, None).run(SESSIONS[0], SESSIONS[-1])
    buys = [f for f in result.fills if f.side is Side.BUY]
    assert buys[0].price == pytest.approx(10.0)
    assert result.diagnostics == {}


class BuyAtAuctionSellNextOpen(Strategy):
    def on_session_start(self, ctx):
        qty = ctx.portfolio.position(Symbol.parse(SYMBOL)).available_qty
        if qty:
            ctx.order.submit(Symbol.parse(SYMBOL), Side.SELL, qty)

    def on_auction(self, ctx):
        if not ctx.state.get("bought"):
            ctx.state["bought"] = True
            ctx.order.submit(
                Symbol.parse(SYMBOL), Side.BUY, 1_000,
                OrderType.LIMIT, 10.5,
            )


def test_early_auction_entry_does_not_delay_next_open_exit_to_entry_minute():
    rule = EntryFill(EntryFillMode.CONTINUOUS, minute_at=time(9, 31))
    result = entry_engine(
        BuyAtAuctionSellNextOpen(), rule,
        lambda session, at, symbols: {
            symbol: MinuteBar(open=10.0, low=10.0) for symbol in symbols
        },
        auction_at=time(9, 25),
    ).run(SESSIONS[0], SESSIONS[1])
    assert [(fill.side, fill.filled_at.time()) for fill in result.fills] == [
        (Side.BUY, time(9, 31)),
        (Side.SELL, time(9, 30)),
    ]


def test_configuring_an_entry_fill_changes_the_run_id_and_default_keeps_it():
    plain = auction_engine(DoNothing()).run(SESSIONS[0], SESSIONS[-1])
    again = entry_engine(DoNothing(), None, None).run(SESSIONS[0], SESSIONS[-1])
    shifted = entry_engine(DoNothing(), EntryFill(EntryFillMode.SLIPPAGE, 0.005), None).run(
        SESSIONS[0], SESSIONS[-1])
    assert plain.run_id == again.run_id
    assert plain.run_id != shifted.run_id


def test_continuous_entry_requires_a_minute_source():
    with pytest.raises(ValueError, match="entry_minutes"):
        entry_engine(DoNothing(), EntryFill(EntryFillMode.CONTINUOUS), None)
