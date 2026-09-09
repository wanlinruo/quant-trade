"""时钟：决定事件从哪来、时间怎么推进。

策略代码对 BacktestClock 与（将来的）LiveClock 完全无感——实盘跑的是
回测验证过的同一条代码路径。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from datetime import date, datetime, time
from enum import StrEnum

from qsys.core.calendar import TradingCalendar


class Phase(StrEnum):
    SESSION_START = "session_start"  # 盘前：当日 bar 尚不可见
    AUCTION = "auction"  # 集合竞价已结算：当日 open 已确定，其余仍不可见
    CHECKPOINT = "checkpoint"  # 盘中某时刻：只有当时及更早的分钟线可见
    BAR = "bar"  # 收盘：当日 bar 可见
    SESSION_END = "session_end"  # 收盘后：记录与复盘


class Clock(ABC):
    @property
    @abstractmethod
    def now(self) -> datetime: ...


class BacktestClock(Clock):
    PRE_OPEN = time(9, 15)
    POST_CLOSE = time(15, 30)

    def __init__(
        self,
        calendar: TradingCalendar,
        start: date,
        end: date,
        checkpoints: Sequence[time] = (),
        auction_at: time | None = None,
    ) -> None:
        self._calendar = calendar
        self._sessions = calendar.sessions_in_range(start, end)
        # 盘中检查点，按时间先后推进。不配则时钟行为与原来完全一致。
        self._checkpoints = tuple(sorted(checkpoints))
        # 集合竞价结算时刻。不配则时钟行为与原来完全一致。
        self._auction_at = auction_at
        self._now: datetime | None = None
        self._session: date | None = None

    @property
    def now(self) -> datetime:
        if self._now is None:
            raise RuntimeError("clock has not started; iterate ticks() first")
        return self._now

    @property
    def session(self) -> date | None:
        return self._session

    def ticks(self) -> Iterator[tuple[datetime, Phase]]:
        for session in self._sessions:
            self._session = session
            moments = [(datetime.combine(session, self.PRE_OPEN), Phase.SESSION_START)]
            if self._auction_at is not None:
                moments.append(
                    (datetime.combine(session, self._auction_at), Phase.AUCTION)
                )
            moments += [
                (datetime.combine(session, at), Phase.CHECKPOINT)
                for at in self._checkpoints
            ]
            moments += [
                (self._calendar.close_time(session), Phase.BAR),
                (datetime.combine(session, self.POST_CLOSE), Phase.SESSION_END),
            ]
            for moment, phase in moments:
                self._now = moment
                yield moment, phase
