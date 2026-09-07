"""影子模式 Broker。

策略与风控全跑，**只记录意图，永不发单**——真钱一分不动。

刻意做成独立的类而非 LiveBroker(shadow=True)：一个能静默决定真钱
动不动的布尔参数太危险，传错一个 False 就从演习变成实盘。
类型不同则不可能传错。
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from qsys.backtest.broker import Broker
from qsys.core.events import MarketSnapshot, Reject
from qsys.core.trading import Fill, Order, OrderIntent
from qsys.live.config import StreamConfig
from qsys.live.messages import OrderCommand
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
    ) -> None:
        self._t = transport
        self._s = streams
        self._gate = risk_gate
        self._risk_ctx = risk_context_factory
        self._journal = journal_stream
        self._intents: list[Order] = []
        self._rejects: list[Reject] = []

    @property
    def intents(self) -> tuple[Order, ...]:
        return tuple(self._intents)

    def submit(self, order: Order) -> None:
        intent = OrderIntent(order.symbol, order.side, order.qty, order.order_type,
                             order.limit_price)
        decision = self._gate.check(intent, self._risk_ctx())
        if not decision.allowed:
            self._rejects.append(
                Reject(order.order_id, order.symbol, order.side, order.qty,
                       decision.reason, order.created_at)
            )
            return

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
        self._intents.clear()
