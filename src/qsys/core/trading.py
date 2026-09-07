"""交易值类型与现金取整。只有值，没有行为。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from qsys.core.types import Symbol

_CENT = Decimal("0.01")


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


def to_cents(amount: float) -> float:
    """现金金额取整到分。

    券商按分记账，每笔现金流动逐笔取整比全程高精度更贴近真实账户，
    也让合成数据断言可以精确到分。用 Decimal 仅为取得正确的四舍五入
    （float 的 round() 在 .5 上是银行家舍入）。
    """
    return float(Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP))


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """策略发出的下单意图，尚未经过约束检查。"""

    symbol: Symbol
    side: Side
    qty: int
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"qty must be positive: {self.qty}")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit order requires limit_price")

    def with_qty(self, qty: int) -> OrderIntent:
        return replace(self, qty=qty)


@dataclass(frozen=True, slots=True)
class Order:
    """经约束检查后提交给 Broker 的订单。"""

    order_id: str
    symbol: Symbol
    side: Side
    qty: int
    order_type: OrderType
    limit_price: float | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    symbol: Symbol
    side: Side
    qty: int
    price: float
    fee: float
    filled_at: datetime

    @property
    def gross(self) -> float:
        """成交金额（不含费用）。"""
        return to_cents(self.price * self.qty)

    @property
    def cash_delta(self) -> float:
        """对现金的影响：买入为负、卖出为正，均已含费用。"""
        if self.side is Side.BUY:
            return to_cents(-(self.gross + self.fee))
        return to_cents(self.gross - self.fee)
