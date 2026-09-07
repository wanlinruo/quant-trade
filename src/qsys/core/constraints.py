"""交易约束系统。

约束只做判定，不修改账户。每条约束只能把数量夹得更小或直接拒绝，
绝不放大。同一份约束代码在两处共用：回测撮合前检查、实盘风控下单前检查——
因此任何约束都不得依赖「当前在回测中」这类信息。

core 不认识「涨跌停 10%」这条规则，只认识「涨跌停」这个概念——
具体数值由 markets/ 注入。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from qsys.core.portfolio import Portfolio
from qsys.core.trading import OrderIntent, Side, to_cents


@dataclass(frozen=True, slots=True)
class Verdict:
    allowed_qty: int
    reason: str = ""

    @property
    def is_rejected(self) -> bool:
        return self.allowed_qty == 0


@dataclass(frozen=True, slots=True)
class ConstraintContext:
    portfolio: Portfolio
    reference_price: float  # 该订单预计成交价
    prev_close: float | None = None  # 涨跌停判定基准
    board: str = "main"
    is_suspended: bool = False


class Constraint(ABC):
    name: str

    @abstractmethod
    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict: ...


class LotSizeConstraint(Constraint):
    name = "lot_size"

    def __init__(self, lot: int = 100) -> None:
        if lot < 1:
            raise ValueError(f"lot must be >= 1: {lot}")
        self._lot = lot

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        # 卖出可不足整手（A 股零股须一次卖清），不夹取
        if intent.side is Side.SELL:
            return Verdict(intent.qty)

        allowed = (intent.qty // self._lot) * self._lot
        if allowed == 0:
            return Verdict(0, f"buy qty {intent.qty} below one lot ({self._lot})")
        if allowed < intent.qty:
            return Verdict(allowed, f"rounded down to lot multiple ({self._lot})")
        return Verdict(allowed)
