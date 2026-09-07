"""策略运行时上下文。

显式注入，不使用全局变量——换来两项实在的收益：策略可单元测试
（注入假 ctx，断言它下了什么单，无需跑回测），多策略并行不互相污染状态。

Context 只暴露 DataFeed，策略永远拿不到 WarehouseQuery——
数据访问的 as_of 由 BoundFeed 强制绑定到 ctx.now。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

import polars as pl
import structlog

from qsys.core.portfolio import Portfolio, Position
from qsys.core.trading import to_cents
from qsys.core.types import Symbol
from qsys.data.feed import Adjustment, DataFeed


class BoundFeed:
    """把 as_of 钉死在构造时刻的数据视图。

    策略不能自己指定 as_of——那等于绕过 PIT 契约。
    """

    def __init__(self, feed: DataFeed, as_of: datetime) -> None:
        self._feed = feed
        self._as_of = as_of

    def get(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        adjustment: Adjustment = Adjustment.NONE,
    ) -> pl.DataFrame:
        return self._feed.get(
            symbols, start, end, as_of=self._as_of, adjustment=adjustment
        )


@dataclass(frozen=True, slots=True)
class PortfolioView:
    """只读账户视图，每次回调前重建。"""

    cash: float
    total_value: float
    _positions: Mapping[Symbol, Position]

    @classmethod
    def of(cls, portfolio: Portfolio, prices: Mapping[Symbol, float]) -> PortfolioView:
        market = 0.0
        for symbol, held in portfolio.positions.items():
            market += held.total_qty * prices.get(symbol, 0.0)
        return cls(
            cash=portfolio.cash,
            total_value=to_cents(portfolio.cash + market),
            _positions={s: replace(p) for s, p in portfolio.positions.items()},
        )

    def position(self, symbol: Symbol) -> Position:
        held = self._positions.get(symbol)
        return replace(held) if held is not None else Position(symbol)

    @property
    def symbols(self) -> frozenset[Symbol]:
        return frozenset(self._positions)


class Context:
    def __init__(
        self,
        clock,
        feed: DataFeed,
        portfolio: PortfolioView,
        order_api,
        *,
        params: dict[str, Any],
        state: dict[str, Any],
        log=None,
    ) -> None:
        self._clock = clock
        self._feed = feed
        self._portfolio = portfolio
        self._order = order_api
        self.params = params
        self.state = state
        self.log = log if log is not None else structlog.get_logger("strategy")

    @property
    def now(self) -> datetime:
        return self._clock.now

    @property
    def data(self) -> BoundFeed:
        return BoundFeed(self._feed, self.now)

    @property
    def portfolio(self) -> PortfolioView:
        return self._portfolio

    @property
    def order(self):
        return self._order
