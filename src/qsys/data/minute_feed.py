"""分钟线的 PIT 契约。

日线的可见时刻是收盘；分钟线的可见时刻就是**这根 bar 自己的收盘时刻**——
存储约定 dt 即该分钟的收盘时刻（09:30–09:31 这根记 09:31），所以可见性
退化成一句 `dt <= as_of`，没有差一分钟的歧义。

策略要在 09:31 看竞价量、在 14:50 看当时价格。只要这里能返回 as_of
之后的 bar，未来函数就会悄悄混进策略——反向测试守着这条。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time

import polars as pl

from qsys.data.query import WarehouseQuery

# QMT 实测的当日 bar 结构（241 根，均以**收盘时刻**标注）：
#   09:30            开盘集合竞价，OHLC 相等，其 open 即日线 open
#   09:31 ~ 11:30    上午连续竞价 120 根
#   13:01 ~ 15:00    下午连续竞价 120 根，末根含收盘集合竞价
# 判定依据：11:30 存在而 13:00 不存在——若按开盘时刻标注必然相反。
#
# SESSION_OPEN 必须是 09:30 而不是 09:31：竞价量正是策略判高开/低开的依据，
# 少一分钟就把它整根丢了。
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(15, 0)


class MinuteFeed:
    def __init__(self, query: WarehouseQuery) -> None:
        self._query = query

    def get(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        *,
        as_of: datetime,
    ) -> pl.DataFrame:
        """返回 [start, min(end, as_of)] 区间内的分钟线。"""
        effective_end = min(end, as_of)
        if effective_end < start:
            return self._query.minute_bars(symbols, start, start).clear()
        return self._query.minute_bars(symbols, start, effective_end)

    def session_bars(
        self, symbols: Sequence[str], day: date, *, as_of: datetime
    ) -> pl.DataFrame:
        """某个交易日当天的全部可见分钟线。"""
        return self.get(
            symbols,
            datetime.combine(day, SESSION_OPEN),
            datetime.combine(day, SESSION_CLOSE),
            as_of=as_of,
        )

    def first_bar(
        self, symbols: Sequence[str], day: date, *, as_of: datetime
    ) -> pl.DataFrame:
        """当日首根分钟线，每个标的一行——即 09:30 的**开盘集合竞价** bar。

        实测：该根 OHLC 四价相等（集合竞价只有一个成交价），其 open 与日线
        open 完全相等。策略判高开/低开用的就是它。
        """
        bars = self.session_bars(symbols, day, as_of=as_of)
        if bars.height == 0:
            return bars
        return (
            bars.sort(["symbol", "dt"])
            .group_by("symbol", maintain_order=True)
            .first()
            .select(bars.columns)
        )
