from datetime import date, datetime

import polars as pl
import pytest

from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol
from qsys.data.feed import Adjustment
from qsys.strategy.context import BoundFeed, Context, PortfolioView

CMB = Symbol("600036", Market.SH)
T = datetime(2024, 1, 3, 15, 0)


class RecordingFeed:
    def __init__(self):
        self.calls = []

    def get(self, symbols, start, end, *, as_of, adjustment=Adjustment.NONE):
        self.calls.append({"symbols": list(symbols), "start": start, "end": end,
                           "as_of": as_of, "adjustment": adjustment})
        return pl.DataFrame({"symbol": [], "dt": []})


class FixedClock:
    def __init__(self, now):
        self._now = now

    @property
    def now(self):
        return self._now


def portfolio_with(qty: int) -> Portfolio:
    p = Portfolio(100_000.0)
    if qty:
        p.apply_fill(Fill("o", CMB, Side.BUY, qty, 10.0, 0.0, T))
    return p


def test_bound_feed_injects_as_of():
    feed = RecordingFeed()
    BoundFeed(feed, T).get([str(CMB)], date(2024, 1, 1), date(2024, 1, 3))
    assert feed.calls[0]["as_of"] == T


def test_bound_feed_forwards_adjustment():
    feed = RecordingFeed()
    BoundFeed(feed, T).get([str(CMB)], date(2024, 1, 1), date(2024, 1, 3),
                           adjustment=Adjustment.BACKWARD)
    assert feed.calls[0]["adjustment"] is Adjustment.BACKWARD


def test_bound_feed_rejects_caller_supplied_as_of():
    """策略不得自己指定 as_of——那等于绕过 PIT 契约。"""
    feed = RecordingFeed()
    with pytest.raises(TypeError):
        BoundFeed(feed, T).get([str(CMB)], date(2024, 1, 1), date(2024, 1, 3), as_of=T)


def test_portfolio_view_exposes_cash_and_total_value():
    view = PortfolioView.of(portfolio_with(100), {CMB: 12.0})
    assert view.cash == 100_000.0 - 1000.0
    assert view.total_value == view.cash + 1200.0


def test_portfolio_view_position_lookup():
    view = PortfolioView.of(portfolio_with(100), {CMB: 12.0})
    assert view.position(CMB).total_qty == 100


def test_portfolio_view_position_of_unheld_is_empty():
    view = PortfolioView.of(portfolio_with(0), {})
    assert view.position(CMB).is_empty is True


def test_portfolio_view_is_frozen():
    view = PortfolioView.of(portfolio_with(100), {CMB: 12.0})
    with pytest.raises(Exception):
        view.cash = 0.0


def test_mutating_a_view_position_does_not_affect_the_account():
    p = portfolio_with(100)
    view = PortfolioView.of(p, {CMB: 12.0})
    view.position(CMB).total_qty = 999
    assert p.position(CMB).total_qty == 100


def test_context_now_comes_from_the_clock():
    ctx = Context(FixedClock(T), RecordingFeed(), PortfolioView.of(portfolio_with(0), {}),
                  object(), params={}, state={})
    assert ctx.now == T


def test_context_data_is_bound_to_now():
    feed = RecordingFeed()
    ctx = Context(FixedClock(T), feed, PortfolioView.of(portfolio_with(0), {}),
                  object(), params={}, state={})
    ctx.data.get([str(CMB)], date(2024, 1, 1), date(2024, 1, 3))
    assert feed.calls[0]["as_of"] == T


def test_context_exposes_no_warehouse_query():
    """完成标准 4：策略层不得触达 WarehouseQuery。"""
    ctx = Context(FixedClock(T), RecordingFeed(), PortfolioView.of(portfolio_with(0), {}),
                  object(), params={}, state={})
    exposed = {name for name in dir(ctx) if not name.startswith("_")}
    assert "query" not in exposed
    assert "warehouse" not in exposed
    assert type(ctx.data).__name__ == "BoundFeed"


def test_context_state_is_mutable_and_persists():
    state = {}
    ctx = Context(FixedClock(T), RecordingFeed(), PortfolioView.of(portfolio_with(0), {}),
                  object(), params={}, state=state)
    ctx.state["seen"] = 1
    assert state["seen"] == 1


def test_context_params_are_readable():
    ctx = Context(FixedClock(T), RecordingFeed(), PortfolioView.of(portfolio_with(0), {}),
                  object(), params={"window": 20}, state={})
    assert ctx.params["window"] == 20


def test_schedule_is_only_available_during_on_init():
    """在别处声明调度不会生效——与其静默无效，不如直接报错。"""
    import pytest

    from qsys.strategy.context import Context

    ctx = Context(_Clock(), None, None, None, params={}, state={})
    with pytest.raises(RuntimeError, match="on_init"):
        ctx.schedule


def test_schedule_is_there_when_the_engine_passes_one():
    from qsys.strategy.context import Context
    from qsys.strategy.schedule import Schedule

    ctx = Context(_Clock(), None, None, None, params={}, state={},
                  schedule=Schedule())
    ctx.schedule.auction("09:30")
    assert ctx.schedule.declared is True


def test_report_reaches_the_injected_run_report():
    from qsys.ops.strategy_log import RunReport

    report = RunReport()
    ctx = Context(_Clock(), None, None, None, params={}, state={}, report=report)
    ctx.report(candidates=37, passed=2, reasons={"高开超过阈值": 12})
    assert report.candidates == 37 and report.passed == 2
    assert report.reasons == {"高开超过阈值": 12}


def test_report_without_injection_is_a_no_op():
    ctx = Context(_Clock(), None, None, None, params={}, state={})
    ctx.report(note="回测与单测里没有记录器，也不能崩")


class _Clock:
    @property
    def now(self):
        from datetime import datetime
        return datetime(2024, 1, 2, 9, 30)
