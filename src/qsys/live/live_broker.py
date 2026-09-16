"""实盘 Broker。

实现与 SimBroker 相同的 Broker 接口——回测与实盘的差异只可能来自
这一个边界，因而可以逐笔归因。

风控闸门在发单前生效；被拦的订单产生 Reject 并记录，绝不静默丢弃。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import structlog
import inspect

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
    OrderStatusEvent,
    OrderCommand,
    RejectEvent,
)
from qsys.live.risk import RiskContext, RiskGate
from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class _PendingOrder:
    order: Order
    remaining_qty: int
    filled_qty: int = 0
    broker_order_id: str | None = None
    reserved_cash: float = 0.0


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
        strategy_id: str = "",
        command_ttl_seconds: float | None = None,
    ) -> None:
        self._t = transport
        self._s = streams
        self._gate = risk_gate
        self._risk_ctx = risk_context_factory
        self._risk_ctx_accepts_intent = len(inspect.signature(risk_context_factory).parameters) > 0
        self._group = group
        self._consumer = consumer
        self._constraints = constraints
        self._strategy_id = strategy_id
        self._command_ttl_seconds = command_ttl_seconds
        self._constraint_ctx = constraint_context_factory
        self._constraint_ctx_accepts_intent = (
            constraint_context_factory is not None
            and len(inspect.signature(constraint_context_factory).parameters) > 0
        )
        self._pending: dict[str, _PendingOrder] = {}
        self._seen_trade_ids: set[str] = set()
        self._delivered: dict[str, set[str]] = {
            streams.fills: set(), streams.rejects: set(), streams.order_status: set(),
        }
        self._uncommitted: dict[str, set[str]] = {
            streams.fills: set(), streams.rejects: set(), streams.order_status: set(),
        }
        self._rejects: list[Reject] = []
        for stream in (streams.fills, streams.rejects, streams.order_status):
            self._t.ensure_group(stream, group, start_id="0-0")

    def submit(self, order: Order) -> None:
        intent = OrderIntent(order.symbol, order.side, order.qty, order.order_type,
                             order.limit_price)
        risk_ctx = (
            self._risk_ctx(intent) if self._risk_ctx_accepts_intent else self._risk_ctx()
        )
        decision = self._gate.check(intent, risk_ctx)
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
            constraint_ctx = (
                self._constraint_ctx(intent) if self._constraint_ctx_accepts_intent
                else self._constraint_ctx()
            )
            result = self._constraints.apply(intent, constraint_ctx)
            if result.is_rejected:
                log.warning("live_broker.constrained", client_order_id=order.order_id,
                            symbol=str(order.symbol), **result.as_log_fields())
                self._rejects.append(
                    Reject(order.order_id, order.symbol, order.side, order.qty,
                           result.binding_reason, order.created_at)
                )
                return
            qty = result.allowed_qty

        if order.side is Side.BUY:
            price = float(order.limit_price or risk_ctx.reference_price)
            reserved = sum(item.reserved_cash for item in self._pending.values())
            available = max(0.0, risk_ctx.portfolio.cash - reserved)
            affordable = int(available // (price * 1.001)) if price > 0 else 0
            qty = min(qty, affordable)
            if self._constraints is not None:
                qty = qty // 100 * 100
            if qty <= 0:
                self._rejects.append(Reject(
                    order.order_id, order.symbol, order.side, order.qty,
                    "cash_reservation: insufficient unreserved cash", order.created_at,
                ))
                return
            reserved_cash = qty * price * 1.001
        else:
            already_reserved = sum(
                item.remaining_qty for item in self._pending.values()
                if item.order.side is Side.SELL and item.order.symbol == order.symbol
            )
            available = max(
                0, risk_ctx.portfolio.position(order.symbol).available_qty - already_reserved
            )
            qty = min(qty, available)
            if qty <= 0:
                self._rejects.append(Reject(
                    order.order_id, order.symbol, order.side, order.qty,
                    "position_reservation: no unreserved sellable shares", order.created_at,
                ))
                return
            reserved_cash = 0.0

        self._t.append(
            self._s.orders,
            OrderCommand(
                client_order_id=order.order_id,
                session_id=order.created_at.date().isoformat(),
                strategy_id=self._strategy_id,
                symbol=str(order.symbol),
                side=order.side.value,
                qty=qty,
                order_type=order.order_type.value,
                limit_price=order.limit_price,
                created_at=order.created_at,
                expires_at=(
                    order.created_at + timedelta(seconds=self._command_ttl_seconds)
                    if self._command_ttl_seconds is not None else None
                ),
            ).to_payload(),
        )
        sent_order = order if qty == order.qty else replace(order, qty=qty)
        self._pending[order.order_id] = _PendingOrder(
            sent_order, qty, reserved_cash=reserved_cash
        )
        log.info("live_broker.submitted", client_order_id=order.order_id,
                 symbol=str(order.symbol), side=order.side.value, qty=qty)

    def on_market_data(self, snapshot: MarketSnapshot | None) -> None:
        """实盘由真实市场撮合，忽略行情推送。"""

    def poll_fills(self) -> tuple[Fill, ...]:
        out: list[Fill] = []
        for msg in self._reports(self._s.fills):
            event = FillEvent.from_payload(msg.payload)
            if event.broker_trade_id and event.broker_trade_id in self._seen_trade_ids:
                continue
            if event.broker_trade_id:
                self._seen_trade_ids.add(event.broker_trade_id)
            pending = self._pending.get(event.client_order_id)
            if pending is not None:
                pending.filled_qty = min(
                    pending.order.qty, pending.filled_qty + event.qty
                )
                pending.remaining_qty = pending.order.qty - pending.filled_qty
                if pending.order.side is Side.BUY and pending.order.qty > 0:
                    initial = pending.order.qty * float(
                        pending.order.limit_price or event.price
                    ) * 1.001
                    pending.reserved_cash = (
                        initial * pending.remaining_qty / pending.order.qty
                    )
                pending.broker_order_id = event.broker_order_id or pending.broker_order_id
                if pending.remaining_qty == 0:
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
                    broker_order_id=event.broker_order_id,
                    broker_trade_id=event.broker_trade_id,
                )
            )
        return tuple(out)

    def poll_rejects(self) -> tuple[Reject, ...]:
        out = list(self._rejects)
        self._rejects.clear()
        for msg in self._reports(self._s.rejects):
            event = RejectEvent.from_payload(msg.payload)
            pending = self._pending.pop(event.client_order_id, None)
            order = pending.order if pending else None
            out.append(
                Reject(
                    order_id=event.client_order_id,
                    symbol=Symbol.parse(event.symbol),
                    side=order.side if order else Side.BUY,
                    qty=(event.unfilled_qty if event.unfilled_qty is not None
                         else pending.remaining_qty if pending else 0),
                    reason=event.reason,
                    at=event.at,
                )
            )
        return tuple(out)

    def pending(self) -> tuple[Order, ...]:
        return tuple(item.order for item in self._pending.values())

    def restore(self, order: Order, remaining_qty: int) -> None:
        """恢复核心重启前仍未终态的委托，不会再次发单。"""
        if remaining_qty <= 0:
            return
        restored = replace(order, qty=remaining_qty)
        reserved = (
            remaining_qty * float(order.limit_price or 0.0) * 1.001
            if order.side is Side.BUY else 0.0
        )
        self._pending[order.order_id] = _PendingOrder(
            restored, remaining_qty, reserved_cash=reserved,
        )

    def remaining(self, client_order_id: str) -> int:
        item = self._pending.get(client_order_id)
        return item.remaining_qty if item is not None else 0

    def poll_statuses(self) -> tuple[OrderStatusEvent, ...]:
        out: list[OrderStatusEvent] = []
        for msg in self._reports(self._s.order_status):
            event = OrderStatusEvent.from_payload(msg.payload)
            pending = self._pending.get(event.client_order_id)
            if pending is not None:
                pending.broker_order_id = event.broker_order_id
                # 成交数量只由逐笔 FillEvent 推进，避免“状态先到、成交后到”时
                # 把同一成交扣两次。撤/废单没有后续成交余量，可以立即释放。
                if event.status in {"cancelled", "rejected"}:
                    self._pending.pop(event.client_order_id, None)
                elif event.status == "filled" and pending.remaining_qty == 0:
                    self._pending.pop(event.client_order_id, None)
            out.append(event)
        return tuple(out)

    def _reports(self, stream: str):
        """先恢复 PEL，再读新消息；同一进程内未提交的消息不重复交付。"""
        messages = self._t.pending(stream, self._group)
        messages += self._t.read_group(stream, self._group, self._consumer, count=100)
        out = []
        for msg in messages:
            if msg.id in self._delivered[stream]:
                continue
            self._delivered[stream].add(msg.id)
            self._uncommitted[stream].add(msg.id)
            out.append(msg)
        return out

    def commit_reports(self) -> None:
        """仅在调用方已将回报及组合状态持久化后确认消息。"""
        for stream, ids in self._uncommitted.items():
            for message_id in tuple(ids):
                self._t.ack(stream, self._group, message_id)
                ids.remove(message_id)
                self._delivered[stream].discard(message_id)

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
