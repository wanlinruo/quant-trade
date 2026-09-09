"""引擎的日内检查点。

本策略是隔夜策略（T 日买、T+1 才可卖），退出发生在 11:25 / 14:25 / 14:50，
而引擎原本只有 09:15 / 15:00 / 15:30 三个时刻。
"""

from datetime import date, datetime, time, timedelta

import pytest

from qsys.backtest.engine import BacktestEngine
from qsys.backtest.fees import ZeroFeeModel
from qsys.backtest.slippage import NoSlippage
from qsys.core.calendar import AShareCalendar
from qsys.core.types import Symbol
from qsys.markets.ashare import ashare_pipeline
from qsys.strategy.base import Strategy
from qsys.core.trading import Side

SESSIONS = [date(2024, 1, d) for d in (2, 3, 4, 5)]
SYMBOL = "600001.SH"
CHECKPOINTS = (time(11, 25), time(14, 25), time(14, 50))


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
