"""实盘 Broker。

实现与 SimBroker 相同的 Broker 接口——回测与实盘的差异只可能来自
这一个边界，因而可以逐笔归因。

风控闸门在发单前生效；被拦的订单产生 Reject 并记录，绝不静默丢弃。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import structlog

from qsys.backtest.broker import Broker
from qsys.core.constraints import ConstraintContext, ConstraintPipeline
from qsys.core.events import MarketSnapshot, Reject
from qsys.core.trading import Fill, Order, OrderIntent, Side
from qsys.core.types import Symbol
from qsys.live.config import StreamConfig
from qsys.live.messages import (
    AccountSnapshot,
    ControlCommand,
    FillEvent,
    OrderCommand,
    RejectEvent,
)
from qsys.live.risk import RiskContext, RiskGate
from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class LiveBroker(Broker):
    def __init__(
        self,
        transport: Transport,
        streams: StreamConfig,
        *,
        risk_gate: RiskGate,
        risk_context_factory: Callable[[], RiskContext],
        group: str = "core",
        consumer: str = "core-1",
        constraints: ConstraintPipeline | None = None,
        constraint_context_factory: Callable[[], ConstraintContext] | None = None,
    ) -> None:
        self._t = transport
        self._s = streams
        self._gate = risk_gate
        self._risk_ctx = risk_context_factory
        self._group = group
        self._consumer = consumer
        self._constraints = constraints
        self._constraint_ctx = constraint_context_factory
        self._pending: dict[str, Order] = {}
        self._rejects: list[Reject] = []
        for stream in (streams.fills, streams.rejects):
            self._t.ensure_group(stream, group)

    def submit(self, order: Order) -> None:
        intent = OrderIntent(order.symbol, order.side, order.qty, order.order_type,
                             order.limit_price)
        decision = self._gate.check(intent, self._risk_ctx())
        if not decision.allowed:
            log.warning("live_broker.blocked", client_order_id=order.order_id,
                        symbol=str(order.symbol), **decision.as_log_fields())
            self._rejects.append(
                Reject(order.order_id, order.symbol, order.side, order.qty,
                       decision.reason, order.created_at)
            )
            return

        # 下单前约束检查：spec §5.3 要求约束在回测撮合与实盘风控两处共用。
        # 依赖券商兜底会让本地持仓推演与实际不符，而对账的前提正是本地推演可信。
        qty = order.qty
        if self._constraints is not None and self._constraint_ctx is not None:
            result = self._constraints.apply(intent, self._constraint_ctx())
            if result.is_rejected:
                log.warning("live_broker.constrained", client_order_id=order.order_id,
                            symbol=str(order.symbol), **result.as_log_fields())
                self._rejects.append(
                    Reject(order.order_id, order.symbol, order.side, order.qty,
                           result.binding_reason, order.created_at)
                )
                return
            qty = result.allowed_qty

        self._t.append(
            self._s.orders,
            OrderCommand(
                client_order_id=order.order_id,
                symbol=str(order.symbol),
                side=order.side.value,
                qty=qty,
                order_type=order.order_type.value,
                limit_price=order.limit_price,
                created_at=order.created_at,
            ).to_payload(),
        )
        self._pending[order.order_id] = order
        log.info("live_broker.submitted", client_order_id=order.order_id,
                 symbol=str(order.symbol), side=order.side.value, qty=qty)

    def on_market_data(self, snapshot: MarketSnapshot | None) -> None:
        """实盘由真实市场撮合，忽略行情推送。"""

    def poll_fills(self) -> tuple[Fill, ...]:
        out: list[Fill] = []
        for msg in self._t.read_group(self._s.fills, self._group, self._consumer, count=100):
            event = FillEvent.from_payload(msg.payload)
            self._t.ack(self._s.fills, self._group, msg.id)
            self._pending.pop(event.client_order_id, None)
            out.append(
                Fill(
                    order_id=event.client_order_id,
                    symbol=Symbol.parse(event.symbol),
                    side=Side(event.side),
                    qty=event.qty,
                    price=event.price,
                    fee=event.fee,
                    filled_at=event.filled_at,
                )
            )
        return tuple(out)

    def poll_rejects(self) -> tuple[Reject, ...]:
        out = list(self._rejects)
        self._rejects.clear()
        for msg in self._t.read_group(self._s.rejects, self._group, self._consumer, count=100):
            event = RejectEvent.from_payload(msg.payload)
            self._t.ack(self._s.rejects, self._group, msg.id)
            order = self._pending.pop(event.client_order_id, None)
            out.append(
                Reject(
                    order_id=event.client_order_id,
                    symbol=Symbol.parse(event.symbol),
                    side=order.side if order else Side.BUY,
                    qty=order.qty if order else 0,
                    reason=event.reason,
                    at=event.at,
                )
            )
        return tuple(out)

    def pending(self) -> tuple[Order, ...]:
        return tuple(self._pending.values())

    def cancel_all(self) -> None:
        self._t.append(
            self._s.control,
            ControlCommand(action="cancel_all", at=datetime.now()).to_payload(),
        )
        self._pending.clear()
        log.warning("live_broker.cancel_all")

    def account_snapshot(self) -> AccountSnapshot | None:
        raw = self._t.get_key(self._s.account)
        if raw is None:
            return None
        return AccountSnapshot.model_validate_json(raw)
