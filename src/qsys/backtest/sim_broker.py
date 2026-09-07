"""SimBroker 保守撮合。

不可妥协的正确性原则：订单在**下一次** on_market_data 时以该 bar 的
开盘价成交。绝不允许用提交订单那一刻的 bar 数据成交——那是回测虚高的
第二大原因（第一是未来函数，已由数据层的 as_of 契约堵死）。

撮合只产生 Fill，不修改账户：账户由引擎负责应用，职责分离。
"""

from __future__ import annotations

from datetime import datetime, time

import structlog

from qsys.backtest.broker import Broker
from qsys.backtest.fees import FeeModel
from qsys.backtest.slippage import SlippageModel
from qsys.core.constraints import ConstraintContext, ConstraintPipeline
from qsys.core.events import MarketSnapshot, Reject
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Order, OrderIntent, to_cents

log = structlog.get_logger(__name__)

_OPEN_TIME = time(9, 30)


class SimBroker(Broker):
    def __init__(
        self,
        portfolio: Portfolio,
        pipeline: ConstraintPipeline,
        fees: FeeModel,
        slippage: SlippageModel,
        *,
        volume_cap: float = 0.05,
    ) -> None:
        if not 0 < volume_cap <= 1:
            raise ValueError(f"volume_cap must be in (0, 1]: {volume_cap}")
        self._portfolio = portfolio
        self._pipeline = pipeline
        self._fees = fees
        self._slippage = slippage
        self._volume_cap = volume_cap
        self._pending: list[Order] = []
        self._fills: list[Fill] = []
        self._rejects: list[Reject] = []

    def submit(self, order: Order) -> None:
        self._pending.append(order)

    def pending(self) -> tuple[Order, ...]:
        return tuple(self._pending)

    def cancel_all(self) -> None:
        self._pending.clear()

    def poll_fills(self) -> tuple[Fill, ...]:
        drained = tuple(self._fills)
        self._fills.clear()
        return drained

    def poll_rejects(self) -> tuple[Reject, ...]:
        drained = tuple(self._rejects)
        self._rejects.clear()
        return drained

    def on_market_data(self, snapshot: MarketSnapshot) -> None:
        at = datetime.combine(snapshot.session, _OPEN_TIME)
        queued, self._pending = self._pending, []
        for order in queued:
            self._match(order, snapshot, at)

    def _reject(self, order: Order, qty: int, reason: str, at: datetime) -> None:
        self._rejects.append(
            Reject(order.order_id, order.symbol, order.side, qty, reason, at)
        )

    def _match(self, order: Order, snapshot: MarketSnapshot, at: datetime) -> None:
        bar = snapshot.bar_of(order.symbol)
        if bar is None:
            self._reject(order, order.qty, "suspended: no bar for this session", at)
            return

        price = self._slippage.adjust(order.side, bar.open)
        ctx = ConstraintContext(
            portfolio=self._portfolio,
            reference_price=price,
            prev_close=snapshot.prev_close_of(order.symbol),
            board=snapshot.board_of(order.symbol),
            is_suspended=False,
        )
        result = self._pipeline.apply(
            OrderIntent(order.symbol, order.side, order.qty, order.order_type,
                        order.limit_price),
            ctx,
        )
        if result.is_rejected:
            self._reject(order, order.qty, result.binding_reason, at)
            return

        qty = result.allowed_qty
        cap = int(bar.volume * self._volume_cap)
        if qty > cap:
            dropped = qty - cap
            qty = cap
            # 被削减的部分作废并告警，不得静默丢弃
            self._reject(
                order, dropped,
                f"volume cap {self._volume_cap:.0%} of {bar.volume}: {dropped} shares dropped",
                at,
            )
            log.warning(
                "sim_broker.volume_capped",
                symbol=str(order.symbol),
                session=str(snapshot.session),
                requested=result.allowed_qty,
                filled=qty,
            )

        # 成交量上限可能把数量削到不足整手，需再过一次约束
        recheck = self._pipeline.apply(
            OrderIntent(order.symbol, order.side, max(qty, 1), order.order_type,
                        order.limit_price),
            ctx,
        )
        qty = min(qty, recheck.allowed_qty)
        if qty <= 0:
            self._reject(order, order.qty, recheck.binding_reason or "no fillable quantity", at)
            return

        gross = to_cents(price * qty)
        fee = self._fees.fee_for(order.side, gross)
        self._fills.append(
            Fill(order.order_id, order.symbol, order.side, qty, price, fee, at)
        )
