"""市场快照与拒单记录。

MarketSnapshot 是引擎在每个交易日交给 Broker 的市场状态。
「当日无 bar」即视为停牌——这是引擎判定停牌的唯一依据。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from qsys.core.trading import Side
from qsys.core.types import Bar, Symbol


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    session: date
    bars: Mapping[Symbol, Bar]
    prev_closes: Mapping[Symbol, float]
    boards: Mapping[Symbol, str]
    # 当日直取涨跌停价 (limit_up, limit_down)，来自 stk_limit；缺某只就是拿不到（D-19）
    limits: Mapping[Symbol, tuple[float, float]] = field(default_factory=dict)

    @property
    def symbols(self) -> frozenset[Symbol]:
        return frozenset(self.bars)

    def bar_of(self, symbol: Symbol) -> Bar | None:
        return self.bars.get(symbol)

    def is_suspended(self, symbol: Symbol) -> bool:
        return symbol not in self.bars

    def prev_close_of(self, symbol: Symbol) -> float | None:
        return self.prev_closes.get(symbol)

    def limit_of(self, symbol: Symbol) -> tuple[float, float] | None:
        return self.limits.get(symbol)

    def board_of(self, symbol: Symbol) -> str:
        return self.boards.get(symbol, "main")


@dataclass(frozen=True, slots=True)
class Reject:
    order_id: str
    symbol: Symbol
    side: Side
    qty: int
    reason: str
    at: datetime
