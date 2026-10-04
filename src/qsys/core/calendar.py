"""交易日历。

`close_time()` 是 PIT 契约的时间基准：日线数据在该时刻之后才可见。
"""

from __future__ import annotations

import bisect
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta


def last_closed_session(calendar: "TradingCalendar", as_of: datetime) -> date | None:
    """as_of 时刻已经收盘的最后一个交易日。

    两处共用，且必须共用：
      · PIT 读取——日线在收盘后才可见
      · 日线采集——**收盘前的当日 bar 是残缺的**，落盘会污染仓库。
        盘中采到的「今日」只有半天成交量，收盘价其实是采集那一刻的价格，
        据此判出的涨停、据涨停汇总的生态因子会一路错下去，且不会报错。
    """
    day = as_of.date()
    if calendar.is_trading_day(day) and as_of >= calendar.close_time(day):
        return day
    try:
        return calendar.prev_session(day)
    except ValueError:
        return None


def last_closed_day_of_month(calendar: "TradingCalendar", year: int, month: int) -> date:
    """该月最后一个**休市日**：从月末往回找，第一个不开盘的日子。

    月度重启演练排在这一天：重启会让整条交易链断 5 分钟左右，交易日做不得。

    多数月份这天是周日，**第二天就开市**——所以演练安排在上午，留一整天反应，
    失败要立刻告警。月末若整周连着开盘（极少，需连续 7 个交易日），会一路回退
    到上一个周末，仍然落在本月内：A 股每周必有周末休市，所以这个函数总有解。
    """
    from calendar import monthrange

    day = date(year, month, monthrange(year, month)[1])
    first = date(year, month, 1)
    while day >= first:
        if not calendar.is_trading_day(day):
            return day
        day -= timedelta(days=1)
    raise ValueError(f"{year}-{month:02d} 整月都是交易日，不可能")


class TradingCalendar(ABC):
    @abstractmethod
    def is_trading_day(self, day: date) -> bool: ...

    @abstractmethod
    def sessions_in_range(self, start: date, end: date) -> list[date]: ...

    @abstractmethod
    def close_time(self, day: date) -> datetime: ...

    @abstractmethod
    def next_session(self, day: date) -> date: ...

    @abstractmethod
    def prev_session(self, day: date) -> date: ...


class AShareCalendar(TradingCalendar):
    CLOSE = time(15, 0)

    def __init__(self, sessions: Iterable[date]) -> None:
        self._sessions = sorted(set(sessions))
        self._lookup = set(self._sessions)

    def is_trading_day(self, day: date) -> bool:
        return day in self._lookup

    def sessions_in_range(self, start: date, end: date) -> list[date]:
        lo = bisect.bisect_left(self._sessions, start)
        hi = bisect.bisect_right(self._sessions, end)
        return self._sessions[lo:hi]

    def close_time(self, day: date) -> datetime:
        if day not in self._lookup:
            raise ValueError(f"{day} is not a trading day")
        return datetime.combine(day, self.CLOSE)

    def next_session(self, day: date) -> date:
        idx = bisect.bisect_right(self._sessions, day)
        if idx >= len(self._sessions):
            raise ValueError(f"{day} is beyond calendar range")
        return self._sessions[idx]

    def prev_session(self, day: date) -> date:
        idx = bisect.bisect_left(self._sessions, day)
        if idx == 0:
            raise ValueError(f"{day} is beyond calendar range")
        return self._sessions[idx - 1]
