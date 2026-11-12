"""全市场快照的盘口覆盖判定（#651），盘前检查、行情网关心跳与健康观察器共用。

2026-11-12：QMT「全推行情」为「最新价」时 get_full_tick 的五档恒为 0，实盘对每笔
委托报 NO_OPPONENT。这里只统计事实：有效报价（有价、未停牌、非指数）中买一或卖一
价大于 0 的数量；涨跌停只剩单边盘口仍算带盘口。不推断、不补值。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time

from qsys.live.messages import MarketQuote

# 连续竞价时段；盘前、午休、收盘后的快照不代表盘口能力。
CONTINUOUS_SESSIONS = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))
ORDER_BOOK_MIN_RATIO = 0.8
INDEX_SYMBOLS = frozenset({"000001.SH", "399001.SZ", "399006.SZ", "000300.SH"})


def in_continuous_session(at: datetime) -> bool:
    clock = at.time()
    return any(start <= clock <= end for start, end in CONTINUOUS_SESSIONS)


def order_book_counts(quotes: Mapping[str, MarketQuote]) -> tuple[int, int]:
    """返回 (带盘口数, 有效报价数)。"""
    valid = [q for symbol, q in quotes.items()
             if symbol not in INDEX_SYMBOLS and q.price > 0 and not q.suspended]
    return sum(q.bid1 > 0 or q.ask1 > 0 for q in valid), len(valid)


ORDER_BOOK_MAX_AGE_S = 120.0


class OrderBookWatch:
    """健康观察器的当日黏性判定。

    只有连续竞价时段内、且快照不超过 120 秒的测量才更新判定；其余时刻（盘前、午休、
    收盘后、心跳缺字段）沿用当日最后一次有效判定，避免收盘时误报“恢复”。跨日清空。
    返回 (是否盘口缺失, 说明)；None 表示当日尚无有效测量。
    """

    def __init__(self) -> None:
        self._verdict: tuple[bool, str] | None = None
        self._day = None

    def update(self, ratio: float | None, at: datetime | None,
               now: datetime) -> tuple[bool | None, str]:
        if self._day != now.date():
            self._verdict, self._day = None, now.date()
        if ratio is not None and at is not None:
            at = at.replace(tzinfo=None)
            age = (now.replace(tzinfo=None) - at).total_seconds()
            if at.date() == now.date() and in_continuous_session(at) \
                    and -1 <= age <= ORDER_BOOK_MAX_AGE_S:
                self._verdict = (
                    ratio < ORDER_BOOK_MIN_RATIO,
                    f"{at:%H:%M:%S} 全市场快照带盘口 {ratio:.1%}"
                    f"（阈值 {ORDER_BOOK_MIN_RATIO:.0%}）",
                )
        if self._verdict is None:
            return None, ""
        return self._verdict
