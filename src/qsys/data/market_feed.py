"""派生数据的 PIT 访问契约。

日线 bar 的可见时刻是收盘，而 `daily_flags` / `market_regime` /
`valuation_snapshot` 描述的也是收盘后事实，因此适用同一条规则。

**唯一的例外是当日开盘价**：`open` 在 09:30 就已确定，与同一根 bar 的
high/low/close 可见时刻不同。策略判低开/高开正是用它。为此单开一个
`session_open()`，**只返回 open**——把整根 bar 放出来就成了绕过 PIT 的后门。

竞价量取自分钟线 09:30 那根（实测 QMT 把开盘集合竞价单独成根，四价相等）。
分钟线只覆盖最近约一年，取不到时**返回空而不是 0**：返回 0 会让
「竞价量不足」的判定静默通过，调用方分不清「没数据」和「量真的不够」。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time

import polars as pl

from qsys.core.calendar import TradingCalendar, last_closed_session
from qsys.data.query import WarehouseQuery

# QMT 的开盘集合竞价 bar 以 09:30 标注（收盘时刻约定）
AUCTION_AT = time(9, 30)


@dataclass(frozen=True, slots=True)
class IntradayQuote:
    price: float  # 该时刻的最新价
    volume: int  # 截至该时刻的累计成交量（手）


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

    def intraday(
        self, symbols: Sequence[str], day: date, at: time
    ) -> dict[str, IntradayQuote]:
        """某时刻的最新价与截至该时刻的累计成交量。

        取不晚于 `at` 的最后一根分钟线。累计量用于成交量上限——拿全日量
        会让盘中撮合放行超出当时实际可成交的数量，是回测乐观的常见来源。

        **超过 as_of 的时刻一律拿不到**：在 11:25 问 14:50 的价是未来函数。
        """
        moment = datetime.combine(day, at)
        if moment > self._as_of:
            return {}
        bars = self._query.intraday_bars(list(symbols), day).filter(
            pl.col("visible_at") <= moment
        )
        if bars.height == 0:
            return {}
        latest = (
            bars.sort(["symbol", "dt"])
            .group_by("symbol", maintain_order=True)
            .agg(
                pl.col("close").last().alias("price"),
                pl.col("volume").sum().alias("volume"),
            )
            .filter(pl.col("price").is_finite() & (pl.col("price") > 0))
        )
        return {
            row["symbol"]: IntradayQuote(float(row["price"]), int(row["volume"]))
            for row in latest.iter_rows(named=True)
        }

    def limit_up_price(self, symbols: Sequence[str], day: date) -> dict[str, float]:
        """当日涨停价，09:30 起可见。

        涨停价在盘前由 Tushare ``stk_limit`` 直取，因此不该像 high/low/close
        那样锁到当日收盘。迁移期仅对未回填日期读取旧派生表。
        """
        if not self._open_is_visible(day):
            return {}
        hit = self._query.price_limits(list(symbols), day, day)
        # 迁移期兼容尚未回填 price_limits_daily 的历史；新表覆盖后不再走这里。
        if hit.height == 0:
            hit = self._query.daily_flags(day, day).filter(
                pl.col("symbol").is_in(list(symbols))
            )
        hit = hit.filter(pl.col("limit_up").is_not_null())
        return dict(zip(hit["symbol"].to_list(), hit["limit_up"].to_list()))

    def suspended_symbols(self, symbols: Sequence[str], day: date) -> set[str]:
        """当日开盘后可见的直取停牌集合。"""
        if not self._open_is_visible(day):
            return set()
        rows = self._query.suspensions(list(symbols), day, day)
        return set(rows.filter(pl.col("suspend_type") == "S")["symbol"].to_list())

    def auction_volume(self, symbols: Sequence[str], day: date) -> dict[str, int]:
        """集合竞价成交量，取自分钟线 09:30 那根。取不到返回空。"""
        if not self._open_is_visible(day):
            return {}
        moment = datetime.combine(day, AUCTION_AT)
        bars = self._query.minute_bars(list(symbols), moment, moment)
        out = dict(zip(bars["symbol"].to_list(), bars["volume"].to_list()))
        rest = [symbol for symbol in symbols if symbol not in out]
        if rest:
            ticks = self._query.auction_ticks(rest, day, day).filter(pl.col("volume") > 0)
            if ticks.height:
                first = ticks.sort("ts").group_by("symbol", maintain_order=True).first()
                out.update(zip(first["symbol"].to_list(), first["volume"].to_list()))
        return out


class MarketFeed:
    def __init__(self, query: WarehouseQuery, calendar: TradingCalendar) -> None:
        self._query = query
        self._calendar = calendar

    def bind(self, as_of: datetime) -> BoundMarket:
        return BoundMarket(self._query, self._calendar, as_of)
