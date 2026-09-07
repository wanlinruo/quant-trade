from datetime import date, datetime

import polars as pl
import pytest

from qsys.backtest.corporate import CorporateAction
from qsys.backtest.engine import BacktestEngine
from qsys.backtest.fees import ZeroFeeModel
from qsys.backtest.slippage import NoSlippage
from qsys.core.calendar import AShareCalendar
from qsys.core.trading import Side
from qsys.core.types import Market, Symbol
from qsys.markets.ashare import ashare_pipeline
from qsys.strategy.base import Strategy

CMB = Symbol("600036", Market.SH)
SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
CAL = AShareCalendar(SESSIONS)

PRICES = {
    SESSIONS[0]: (10.0, 10.5, 9.8, 10.0),
    SESSIONS[1]: (10.0, 10.6, 9.9, 10.5),
    SESSIONS[2]: (10.5, 11.2, 10.4, 11.0),
    SESSIONS[3]: (11.0, 11.5, 10.8, 11.2),
}


class StubFeed:
    """按 as_of 过滤的最小 DataFeed 实现，语义与 HistoricalFeed 一致。"""

    def __init__(self, prices=PRICES, symbol=CMB):
        self._prices = prices
        self._symbol = symbol
        self.as_of_seen: list[datetime] = []

    def get(self, symbols, start, end, *, as_of, adjustment=None):
        self.as_of_seen.append(as_of)
        rows = []
        for session, (o, h, l, c) in sorted(self._prices.items()):
            if not (start <= session <= end):
                continue
            if as_of < datetime.combine(session, datetime.min.time()).replace(hour=15):
                continue
            if str(self._symbol) not in symbols:
                continue
            rows.append({"symbol": str(self._symbol), "dt": session, "open": o,
                         "high": h, "low": l, "close": c, "volume": 1_000_000,
                         "amount": c * 1_000_000})
        return pl.DataFrame(
            rows,
            schema={"symbol": pl.Utf8, "dt": pl.Date, "open": pl.Float64,
                    "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
                    "volume": pl.Int64, "amount": pl.Float64},
        )


def engine(strategy: Strategy, **kw) -> BacktestEngine:
    defaults = dict(
        feed=StubFeed(),
        calendar=CAL,
        universe=[CMB],
        initial_cash=100_000.0,
        pipeline=ashare_pipeline(),
        fees=ZeroFeeModel(),
        slippage=NoSlippage(),
    )
    return BacktestEngine(strategy, **{**defaults, **kw})


class BuyFirstBar(Strategy):
    def on_bar(self, ctx, bars):
        if not ctx.portfolio.symbols:
            ctx.order.submit(CMB, Side.BUY, 1000)


def test_run_produces_an_equity_curve_per_session():
    result = engine(BuyFirstBar()).run(SESSIONS[0], SESSIONS[-1])
    assert result.equity.height == len(SESSIONS)
    assert result.equity["dt"].to_list() == SESSIONS


def test_signal_on_bar_t_fills_at_open_of_t_plus_one():
    """核心正确性原则的端到端验证。"""
    result = engine(BuyFirstBar()).run(SESSIONS[0], SESSIONS[-1])
    assert len(result.fills) == 1
    fill = result.fills[0]
    assert fill.filled_at.date() == SESSIONS[1]
    assert fill.price == PRICES[SESSIONS[1]][0]


def test_no_fill_before_the_first_signal():
    result = engine(BuyFirstBar()).run(SESSIONS[0], SESSIONS[-1])
    first_equity = result.equity.row(0, named=True)
    assert first_equity["cash"] == 100_000.0


def test_final_value_reflects_position_marked_to_close():
    result = engine(BuyFirstBar()).run(SESSIONS[0], SESSIONS[-1])
    assert result.final_value == pytest.approx(90_000.0 + 11_200.0)


def test_strategy_cannot_see_future_data():
    """通过 ctx.data 取数时 as_of 恒等于 ctx.now。"""
    seen = []

    class Peeker(Strategy):
        def on_bar(self, ctx, bars):
            df = ctx.data.get([str(CMB)], SESSIONS[0], SESSIONS[-1])
            seen.append((ctx.now.date(), df["dt"].max()))

    engine(Peeker()).run(SESSIONS[0], SESSIONS[-1])
    for session, latest in seen:
        assert latest <= session, f"{session} 看到了 {latest}"


def test_session_start_cannot_see_todays_bar():
    seen = []

    class PreOpenPeeker(Strategy):
        def on_session_start(self, ctx):
            df = ctx.data.get([str(CMB)], SESSIONS[0], SESSIONS[-1])
            seen.append((ctx.now.date(), None if df.height == 0 else df["dt"].max()))

    engine(PreOpenPeeker()).run(SESSIONS[0], SESSIONS[-1])
    for session, latest in seen:
        assert latest is None or latest < session


def test_t_plus_one_is_enforced_end_to_end():
    """当日买入当日卖出的意图必须被拒。"""

    class BuyThenSellSameDay(Strategy):
        def on_fill(self, ctx, fill):
            if fill.side is Side.BUY:
                ctx.order.submit(CMB, Side.SELL, fill.qty)

        def on_bar(self, ctx, bars):
            if not ctx.portfolio.symbols and not ctx.state.get("bought"):
                ctx.state["bought"] = True
                ctx.order.submit(CMB, Side.BUY, 1000)

    result = engine(BuyThenSellSameDay()).run(SESSIONS[0], SESSIONS[-1])
    sells = [f for f in result.fills if f.side is Side.SELL]
    assert all(f.filled_at.date() > result.fills[0].filled_at.date() for f in sells)


def test_on_fill_is_called_for_each_fill():
    calls = []

    class Recorder(BuyFirstBar):
        def on_fill(self, ctx, fill):
            calls.append(fill.qty)

    engine(Recorder()).run(SESSIONS[0], SESSIONS[-1])
    assert calls == [1000]


def test_corporate_action_credits_cash():
    actions = {SESSIONS[2]: [CorporateAction(CMB, SESSIONS[2], 0.5, 0.0)]}
    result = engine(BuyFirstBar(), corporate_actions=actions).run(SESSIONS[0], SESSIONS[-1])
    assert result.final_value == pytest.approx(90_000.0 + 500.0 + 11_200.0)


def test_corporate_action_stock_dividend_increases_holding():
    actions = {SESSIONS[2]: [CorporateAction(CMB, SESSIONS[2], 0.0, 1.0)]}
    result = engine(BuyFirstBar(), corporate_actions=actions).run(SESSIONS[0], SESSIONS[-1])
    assert result.final_value == pytest.approx(90_000.0 + 2000 * 11.2)


def test_run_id_is_stable_for_identical_inputs():
    a = engine(BuyFirstBar(), params={"w": 20}).run(SESSIONS[0], SESSIONS[-1])
    b = engine(BuyFirstBar(), params={"w": 20}).run(SESSIONS[0], SESSIONS[-1])
    assert a.run_id == b.run_id


def test_run_id_changes_with_params():
    a = engine(BuyFirstBar(), params={"w": 20}).run(SESSIONS[0], SESSIONS[-1])
    b = engine(BuyFirstBar(), params={"w": 30}).run(SESSIONS[0], SESSIONS[-1])
    assert a.run_id != b.run_id


def test_equity_curve_columns():
    result = engine(BuyFirstBar()).run(SESSIONS[0], SESSIONS[-1])
    assert result.equity.columns == ["dt", "cash", "market_value", "total_value"]


def test_empty_strategy_keeps_initial_cash():
    result = engine(Strategy()).run(SESSIONS[0], SESSIONS[-1])
    assert result.final_value == 100_000.0
    assert result.fills == ()
