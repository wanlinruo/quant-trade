"""派生数据的 PIT 访问契约。

日线 bar 的可见时刻是收盘，而 `daily_flags` / `market_regime` /
`valuation_snapshot` 都从日线派生，因此适用同一条规则。

**唯一的例外是当日开盘价**：`open` 在 09:30 就已确定，与同一根 bar 的
high/low/close 可见时刻不同。策略判低开/高开正是用它。为此单开一个
`session_open()`，**只返回 open**——把整根 bar 放出来就成了绕过 PIT 的后门。

竞价量取自分钟线 09:30 那根（实测 QMT 把开盘集合竞价单独成根，四价相等）。
分钟线只覆盖最近约一年，取不到时**返回空而不是 0**：返回 0 会让
「竞价量不足」的判定静默通过，调用方分不清「没数据」和「量真的不够」。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time

import polars as pl

from qsys.core.calendar import TradingCalendar, last_closed_session
from qsys.data.query import WarehouseQuery

# QMT 的开盘集合竞价 bar 以 09:30 标注（收盘时刻约定）
AUCTION_AT = time(9, 30)


class BoundMarket:
    """as_of 钉死的派生数据视图。策略拿不到别的 as_of。"""

    def __init__(
        self, query: WarehouseQuery, calendar: TradingCalendar, as_of: datetime
    ) -> None:
        self._query = query
        self._calendar = calendar
        self._as_of = as_of

    def _clip(self, start: date, end: date) -> tuple[date, date] | None:
        through = last_closed_session(self._calendar, self._as_of)
        if through is None:
            return None
        effective = min(end, through)
        return None if effective < start else (start, effective)

    def flags(self, symbols: Sequence[str], start: date, end: date) -> pl.DataFrame:
        window = self._clip(start, end)
        if window is None:
            return self._query.daily_flags(start, start).clear()
        return self._query.daily_flags(*window).filter(
            pl.col("symbol").is_in(list(symbols))
        )

    def regime(self, start: date, end: date) -> pl.DataFrame:
        window = self._clip(start, end)
        if window is None:
            return self._query.market_regime(start, start).clear()
        return self._query.market_regime(*window)

    def valuation(self, symbols: Sequence[str], start: date, end: date) -> pl.DataFrame:
        window = self._clip(start, end)
        if window is None:
            return self._query.valuation(start, start).clear()
        return self._query.valuation(*window).filter(
            pl.col("symbol").is_in(list(symbols))
        )

    def _open_is_visible(self, day: date) -> bool:
        return self._as_of >= datetime.combine(day, AUCTION_AT)

    def session_open(self, symbols: Sequence[str], day: date) -> dict[str, float]:
        """当日开盘价，09:30 起可见。**只返回 open。**"""
        if not self._open_is_visible(day):
            return {}
        bars = self._query.daily_bars(list(symbols), day, day)
        if bars.height == 0:
            return {}
        return dict(zip(bars["symbol"].to_list(), bars["open"].to_list()))

    def auction_volume(self, symbols: Sequence[str], day: date) -> dict[str, int]:
        """集合竞价成交量，取自分钟线 09:30 那根。取不到返回空。"""
        if not self._open_is_visible(day):
            return {}
        moment = datetime.combine(day, AUCTION_AT)
        bars = self._query.minute_bars(list(symbols), moment, moment)
        if bars.height == 0:
            return {}
        return dict(zip(bars["symbol"].to_list(), bars["volume"].to_list()))


class MarketFeed:
    def __init__(self, query: WarehouseQuery, calendar: TradingCalendar) -> None:
        self._query = query
        self._calendar = calendar

    def bind(self, as_of: datetime) -> BoundMarket:
        return BoundMarket(self._query, self._calendar, as_of)
