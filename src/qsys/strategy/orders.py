"""下单接口。

两层：低层 submit 精确控制；高层 target_* 声明目标状态。
高层在内部转成低层，最终数量的整手取整与资金检查由约束系统在撮合前完成——
因此 target_weight 是近似的，实际成交数量以约束判定为准。
"""

from __future__ import annotations

from collections.abc import Mapping

from qsys.core.trading import OrderIntent, OrderType, Side
from qsys.core.types import Symbol
from qsys.strategy.context import PortfolioView


class OrderApi:
    def __init__(
        self, portfolio: PortfolioView, prices: Mapping[Symbol, float]
    ) -> None:
        self._portfolio = portfolio
        self._prices = prices
        self._queue: list[OrderIntent] = []

    def drain(self) -> tuple[OrderIntent, ...]:
        drained = tuple(self._queue)
        self._queue.clear()
        return drained

    def submit(
        self,
        symbol: Symbol,
        side: Side,
        qty: int,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
    ) -> None:
        self._queue.append(OrderIntent(symbol, side, qty, order_type, limit_price))

    def _price_of(self, symbol: Symbol) -> float:
        price = self._prices.get(symbol)
        if price is None or price <= 0:
            raise KeyError(f"no price for {symbol}; cannot size the order")
        return price

    def target_qty(self, symbol: Symbol, qty: int) -> None:
        if qty < 0:
            raise ValueError(f"target qty must be >= 0: {qty}")
        current = self._portfolio.position(symbol).total_qty
        diff = qty - current
        if diff == 0:
            return
        side = Side.BUY if diff > 0 else Side.SELL
        self.submit(symbol, side, abs(diff))

    def target_value(self, symbol: Symbol, value: float) -> None:
        if value < 0:
            raise ValueError(f"target value must be >= 0: {value}")
        self.target_qty(symbol, int(value / self._price_of(symbol)))

    def target_weight(self, symbol: Symbol, weight: float) -> None:
        if not 0.0 <= weight <= 1.0:
            raise ValueError(f"weight must be in [0, 1]: {weight}")
        self.target_value(symbol, self._portfolio.total_value * weight)
