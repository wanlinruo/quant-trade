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


class NoShortConstraint(Constraint):
    """不可做空：卖出数量不得超过持仓。"""

    name = "no_short"

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        if intent.side is Side.BUY:
            return Verdict(intent.qty)

        held = ctx.portfolio.position(intent.symbol).total_qty
        if held <= 0:
            return Verdict(0, f"no position to sell in {intent.symbol}")
        if intent.qty > held:
            return Verdict(held, f"clamped to holding {held}")
        return Verdict(intent.qty)


class CashConstraint(Constraint):
    """可用资金：买入数量不得超过现金所能负担。

    fee_buffer 为费用预留比例——下单时费用尚未确定，留出余量避免成交后透支。
    """

    name = "cash"

    def __init__(self, fee_buffer: float = 0.001) -> None:
        if fee_buffer < 0:
            raise ValueError(f"fee_buffer must be >= 0: {fee_buffer}")
        self._buffer = fee_buffer

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        if intent.side is Side.SELL:
            return Verdict(intent.qty)

        unit = ctx.reference_price * (1.0 + self._buffer)
        if unit <= 0:
            return Verdict(0, f"invalid reference price: {ctx.reference_price}")

        affordable = int(ctx.portfolio.cash // unit)
        if affordable <= 0:
            return Verdict(
                0, f"insufficient cash: {ctx.portfolio.cash:.2f} cannot buy one share"
            )
        if intent.qty > affordable:
            return Verdict(affordable, f"clamped by cash to {affordable}")
        return Verdict(intent.qty)


class SettlementConstraint(Constraint):
    """交收约束：卖出数量不得超过已解冻（已交收）的份额。

    A 股装配此约束即为 T+1；T+0 市场不装配，或由调用方在每次成交后
    立即 unfreeze()。
    """

    name = "settlement"

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        if intent.side is Side.BUY:
            return Verdict(intent.qty)

        available = ctx.portfolio.position(intent.symbol).available_qty
        if available <= 0:
            return Verdict(0, f"no settled shares in {intent.symbol}")
        if intent.qty > available:
            return Verdict(available, f"clamped to settled {available}")
        return Verdict(intent.qty)


class SuspensionConstraint(Constraint):
    """停牌不可交易，买卖皆拒。"""

    name = "suspension"

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        if ctx.is_suspended:
            return Verdict(0, f"{intent.symbol} is suspended")
        return Verdict(intent.qty)


class PriceLimitConstraint(Constraint):
    """涨跌停约束。

    方向性是最容易写反的地方：
      涨停 —— 买单排队买不到，卖单成交得掉 -> 拒买、放行卖
      跌停 —— 卖单排队卖不掉，买单成交得到 -> 拒卖、放行买
    """

    name = "price_limit"

    def __init__(self, limits: Mapping[str, float], default_limit: float = 0.10) -> None:
        self._limits = dict(limits)
        self._default = default_limit

    @staticmethod
    def limit_prices(prev_close: float, limit: float) -> tuple[float, float]:
        """返回 (涨停价, 跌停价)，均四舍五入到分。"""
        return to_cents(prev_close * (1.0 + limit)), to_cents(prev_close * (1.0 - limit))

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        if ctx.prev_close is None or ctx.prev_close <= 0:
            # 上市首日等无前收的情形不判定，交由其它约束处理
            return Verdict(intent.qty)

        limit = self._limits.get(ctx.board, self._default)
        up, down = self.limit_prices(ctx.prev_close, limit)
        price = to_cents(ctx.reference_price)

        if intent.side is Side.BUY and price >= up:
            return Verdict(0, f"limit up at {up:.2f}: buy cannot fill")
        if intent.side is Side.SELL and price <= down:
            return Verdict(0, f"limit down at {down:.2f}: sell cannot fill")
        return Verdict(intent.qty)
