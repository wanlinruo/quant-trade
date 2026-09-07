"""滑点模型。

买入向上、卖出向下——滑点永远对你不利。
五期会加入基于实盘对账校准的经验模型。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from qsys.core.trading import Side, to_cents

_MIN_PRICE = 0.01


class SlippageModel(ABC):
    @abstractmethod
    def adjust(self, side: Side, price: float) -> float:
        """返回考虑滑点后的成交价。"""


class NoSlippage(SlippageModel):
    def adjust(self, side: Side, price: float) -> float:
        return price


class FixedRateSlippage(SlippageModel):
    def __init__(self, rate: float) -> None:
        if rate < 0:
            raise ValueError(f"rate must be >= 0: {rate}")
        self._rate = rate

    def adjust(self, side: Side, price: float) -> float:
        factor = 1.0 + self._rate if side is Side.BUY else 1.0 - self._rate
        return max(_MIN_PRICE, to_cents(price * factor))


class FixedTickSlippage(SlippageModel):
    def __init__(self, ticks: int, tick_size: float = 0.01) -> None:
        if ticks < 0:
            raise ValueError(f"ticks must be >= 0: {ticks}")
        self._offset = ticks * tick_size

    def adjust(self, side: Side, price: float) -> float:
        shifted = price + self._offset if side is Side.BUY else price - self._offset
        return max(_MIN_PRICE, to_cents(shifted))
