"""交易日历。

`close_time()` 是 PIT 契约的时间基准：日线数据在该时刻之后才可见。
"""

from __future__ import annotations

import bisect
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import date, datetime, time


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
