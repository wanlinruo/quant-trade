"""Windows 执行代理。

**这里不放任何策略逻辑。** 只做三件事：
  1. 把标准化指令翻译成券商调用
  2. 把回报翻译回标准化事件
  3. 定期发布账户快照与心跳

代码量应小到可一眼读完——它跑在难调试的 Windows 上。
"""

from __future__ import annotations

from datetime import datetime

import structlog

from qsys.agent.adapter import BrokerAdapter
from qsys.live.config import StreamConfig
from qsys.live.idempotency import IdempotencyGuard
from qsys.live.messages import (
    AccountSnapshot,
    ControlCommand,
    FillEvent,
    Heartbeat,
    OrderCommand,
    RejectEvent,
)
from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class ExecutionAgent:
    def __init__(
        self,
        transport: Transport,
        adapter: BrokerAdapter,
        streams: StreamConfig,
        *,
        group: str = "agent",
        consumer: str = "agent-1",
        guard: IdempotencyGuard | None = None,
    ) -> None:
        self._t = transport
        self._adapter = adapter
        self._s = streams
        self._group = group
        self._consumer = consumer
        self._guard = guard or IdempotencyGuard(transport)
        for stream in (streams.orders, streams.control):
            self._t.ensure_group(stream, group)

    def run_once(self) -> int:
        """处理一批指令，返回处理条数。"""
        return self._drain_orders() + self._drain_control()

    def _drain_orders(self) -> int:
        handled = 0
        for msg in self._t.read_group(self._s.orders, self._group, self._consumer, count=50):
            handled += 1
            self._t.ack(self._s.orders, self._group, msg.id)
            try:
                command = OrderCommand.from_payload(msg.payload)
            except Exception as exc:  # 畸形消息不应让代理崩溃
                log.error("agent.malformed_order", error=str(exc), payload=msg.payload)
                continue
            self._execute(command)
        return handled

    def _execute(self, command: OrderCommand) -> None:
        def place() -> str:
            return self._adapter.place_order(
                command.symbol, command.side, command.qty,
                command.order_type, command.limit_price,
            )

        try:
            broker_order_id, replayed = self._guard.process_once(
                command.client_order_id, place
            )
        except Exception as exc:
            # 失败不记入已处理集合，允许重试
            log.error("agent.place_failed", client_order_id=command.client_order_id,
                      error=str(exc))
            self._t.append(
                self._s.rejects,
                RejectEvent(
                    client_order_id=command.client_order_id,
                    symbol=command.symbol,
                    reason=str(exc),
                    at=datetime.now(),
                ).to_payload(),
            )
            return

        if replayed:
            log.info("agent.duplicate_ignored", client_order_id=command.client_order_id)
            return

        self._t.append(
            self._s.fills,
            FillEvent(
                client_order_id=command.client_order_id,
                symbol=command.symbol,
                side=command.side,
                qty=command.qty,
                price=command.limit_price or 0.0,
                fee=0.0,
                filled_at=datetime.now(),
                broker_order_id=broker_order_id,
            ).to_payload(),
        )
        log.info("agent.placed", client_order_id=command.client_order_id,
                 broker_order_id=broker_order_id)

    def _drain_control(self) -> int:
        handled = 0
        for msg in self._t.read_group(self._s.control, self._group, self._consumer, count=50):
            handled += 1
            self._t.ack(self._s.control, self._group, msg.id)
            try:
                command = ControlCommand.from_payload(msg.payload)
            except Exception as exc:
                log.error("agent.malformed_control", error=str(exc))
                continue
            if command.action == "cancel_all":
                self._adapter.cancel_all()
                log.warning("agent.cancelled_all", reason=command.reason)
            elif command.action == "query":
                self.publish_account()
        return handled

    def publish_account(self) -> None:
        cash, positions = self._adapter.query_account()
        snapshot = AccountSnapshot(at=datetime.now(), cash=cash, positions=positions)
        self._t.set_key(self._s.account, snapshot.model_dump_json())

    def beat(self) -> None:
        self._t.set_key(
            f"{self._s.heartbeat}:agent",
            Heartbeat(source="agent", at=datetime.now()).model_dump_json(),
        )
