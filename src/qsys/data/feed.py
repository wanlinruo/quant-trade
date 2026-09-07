"""PIT 数据访问契约。

日线 bar 的可见时刻 = 该交易日的收盘时刻。`get()` 只返回可见时刻 <= as_of
的数据，使未来函数在 API 层面不可能发生，而非依赖策略作者自觉。

数据层不得再暴露任何无 as_of 的历史数据读取函数。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum

import polars as pl

from qsys.core.calendar import TradingCalendar
from qsys.data.query import WarehouseQuery

PRICE_COLUMNS = ("open", "high", "low", "close")


class Adjustment(StrEnum):
    NONE = "none"
    BACKWARD = "backward"  # 后复权
    FORWARD = "forward"  # 前复权，以 as_of 时可见的最后一根 bar 为基准


class DataFeed(ABC):
    @abstractmethod
    def get(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        as_of: datetime,
        adjustment: Adjustment = Adjustment.NONE,
    ) -> pl.DataFrame: ...


class HistoricalFeed(DataFeed):
    def __init__(self, query: WarehouseQuery, calendar: TradingCalendar) -> None:
        self._query = query
        self._calendar = calendar

    def _last_visible_session(self, as_of: datetime) -> date | None:
        """as_of 时刻已收盘的最后一个交易日。"""
        day = as_of.date()
        if self._calendar.is_trading_day(day) and as_of >= self._calendar.close_time(day):
            return day
        try:
            return self._calendar.prev_session(day)
        except ValueError:
            return None

    def get(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        as_of: datetime,
        adjustment: Adjustment = Adjustment.NONE,
    ) -> pl.DataFrame:
        visible_through = self._last_visible_session(as_of)
        if visible_through is None:
            return self._query.daily_bars(symbols, start, start).clear()

        effective_end = min(end, visible_through)
        if effective_end < start:
            return self._query.daily_bars(symbols, start, start).clear()

        bars = self._query.daily_bars(symbols, start, effective_end)
        if adjustment is Adjustment.NONE or bars.height == 0:
            return bars

        factors = self._query.adj_factors(symbols)
        if factors.height == 0:
            return bars

        joined = bars.join(factors, on=["symbol", "dt"], how="left").with_columns(
            pl.col("cum_factor").fill_null(1.0)
        )

        if adjustment is Adjustment.FORWARD:
            anchor = joined.group_by("symbol").agg(
                pl.col("cum_factor").sort_by("dt").last().alias("anchor")
            )
            joined = joined.join(anchor, on="symbol", how="left").with_columns(
                (pl.col("cum_factor") / pl.col("anchor")).alias("cum_factor")
            )

        return (
            joined.with_columns(
                [(pl.col(c) * pl.col("cum_factor")).alias(c) for c in PRICE_COLUMNS]
            )
            .select(bars.columns)
            .sort(["symbol", "dt"])
        )
