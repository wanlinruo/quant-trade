"""影子模式 Broker。

策略与风控全跑，**只记录意图，永不发单**——真钱一分不动。

刻意做成独立的类而非 LiveBroker(shadow=True)：一个能静默决定真钱
动不动的布尔参数太危险，传错一个 False 就从演习变成实盘。
类型不同则不可能传错。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import inspect

import structlog

from qsys.backtest.broker import Broker
from qsys.core.constraints import ConstraintContext, ConstraintPipeline
from qsys.core.events import MarketSnapshot, Reject
from qsys.core.trading import Fill, Order, OrderIntent
from qsys.live.config import StreamConfig
from qsys.live.messages import OrderCommand, OrderStatusEvent
from qsys.live.risk import RiskContext, RiskGate
from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class ShadowBroker(Broker):
    def __init__(
        self,
        transport: Transport,
        streams: StreamConfig,
        *,
        risk_gate: RiskGate,
        risk_context_factory: Callable[[], RiskContext],
        journal_stream: str = "shadow:intents",
        constraints: ConstraintPipeline | None = None,
        constraint_context_factory: Callable[[], ConstraintContext] | None = None,
    ) -> None:
        self._t = transport
        self._s = streams
        self._gate = risk_gate
        self._risk_ctx = risk_context_factory
        self._risk_ctx_accepts_intent = len(
            inspect.signature(risk_context_factory).parameters
        ) > 0
        self._journal = journal_stream
        self._constraints = constraints
        self._constraint_ctx = constraint_context_factory
        self._constraint_ctx_accepts_intent = (
            constraint_context_factory is not None
            and len(inspect.signature(constraint_context_factory).parameters) > 0
        )
        self._intents: list[Order] = []
        self._rejects: list[Reject] = []
        self._statuses: list[OrderStatusEvent] = []

    @property
    def intents(self) -> tuple[Order, ...]:
        return tuple(self._intents)

    def submit(self, order: Order) -> None:
        intent = OrderIntent(order.symbol, order.side, order.qty, order.order_type,
                             order.limit_price)
        risk_ctx = (
            self._risk_ctx(intent) if self._risk_ctx_accepts_intent else self._risk_ctx()
        )
        decision = self._gate.check(intent, risk_ctx)
        if not decision.allowed:
            self._rejects.append(
                Reject(order.order_id, order.symbol, order.side, order.qty,
                       decision.reason, order.created_at)
            )
            return

        if self._constraints is not None and self._constraint_ctx is not None:
            constraint_ctx = (
                self._constraint_ctx(intent)
                if self._constraint_ctx_accepts_intent else self._constraint_ctx()
            )
            result = self._constraints.apply(intent, constraint_ctx)
            if result.is_rejected:
                self._rejects.append(
                    Reject(order.order_id, order.symbol, order.side, order.qty,
                           result.binding_reason, order.created_at)
                )
                return
            if result.allowed_qty != order.qty:
                order = replace(order, qty=result.allowed_qty)

        # 只写影子日志，绝不写 orders 流
        self._t.append(
            self._journal,
            OrderCommand(
                client_order_id=order.order_id,
                symbol=str(order.symbol),
                side=order.side.value,
                qty=order.qty,
                order_type=order.order_type.value,
                limit_price=order.limit_price,
                created_at=order.created_at,
            ).to_payload(),
        )
        self._intents.append(order)
        log.info("shadow.intent", client_order_id=order.order_id,
                 symbol=str(order.symbol), side=order.side.value, qty=order.qty)

    def on_market_data(self, snapshot: MarketSnapshot | None) -> None:
        """影子模式不撮合。"""

    def poll_fills(self) -> tuple[Fill, ...]:
        return ()

    def poll_rejects(self) -> tuple[Reject, ...]:
        drained = tuple(self._rejects)
        self._rejects.clear()
        return drained

    def pending(self) -> tuple[Order, ...]:
        return tuple(self._intents)

    def cancel_all(self) -> None:
        self._statuses.extend(
            OrderStatusEvent(
                client_order_id=order.order_id,
                broker_order_id="shadow",
                symbol=str(order.symbol),
                side=order.side.value,
                ordered_qty=order.qty,
                filled_qty=0,
                remaining_qty=order.qty,
                status="cancelled",
                at=datetime.now(),
                reason="shadow session end",
            )
            for order in self._intents
        )
        self._intents.clear()

    def remaining(self, client_order_id: str) -> int:
        return next(
            (order.qty for order in self._intents if order.order_id == client_order_id),
            0,
        )

    def restore(self, order: Order, remaining_qty: int) -> None:
        if remaining_qty > 0 and self.remaining(order.order_id) == 0:
            self._intents.append(replace(order, qty=remaining_qty))

    def poll_statuses(self) -> tuple[OrderStatusEvent, ...]:
        statuses = tuple(self._statuses)
        self._statuses.clear()
        return statuses

    def commit_reports(self) -> None:
        """影子模式没有远端回报需要 ACK。"""
