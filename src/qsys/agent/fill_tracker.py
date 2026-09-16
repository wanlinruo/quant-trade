"""把券商委托/成交快照转换为可幂等发布的标准事件。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from qsys.backtest.fees import FeeModel
from qsys.core.trading import Side, to_cents
from qsys.live.messages import FillEvent, OrderStatusEvent, RejectEvent


TERMINAL_STATUSES = frozenset({52, 53, 54, 56, 57})
STATUS_NAMES = {
    48: "未报", 49: "待报", 50: "已报", 51: "已报待撤", 52: "部成待撤",
    53: "部撤", 54: "已撤", 55: "部成", 56: "已成", 57: "废单",
}


@dataclass(frozen=True, slots=True)
class BrokerTrade:
    traded_id: str
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: str
    price: float
    qty: int
    at: datetime


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    ordered_qty: int
    traded_qty: int
    status: int
    status_msg: str = ""


@dataclass(frozen=True, slots=True)
class TrackerOutput:
    fills: tuple[FillEvent, ...]
    rejects: tuple[RejectEvent, ...]
    statuses: tuple[OrderStatusEvent, ...]
    new_trade_ids: tuple[str, ...]
    closed_order_ids: tuple[str, ...]


class FillTracker:
    def __init__(self, fee_model: FeeModel) -> None:
        self._fees = fee_model

    def observe(
        self,
        trades: list[BrokerTrade],
        orders: list[BrokerOrder],
        *,
        seen_trade_ids: frozenset[str],
        closed_order_ids: frozenset[str],
        now: datetime,
    ) -> TrackerOutput:
        fills: list[FillEvent] = []
        rejects: list[RejectEvent] = []
        statuses: list[OrderStatusEvent] = []
        new_trade_ids: list[str] = []
        closed: list[str] = []

        # 费用按一笔委托的累计成交额计算。每条成交只承担累计费用增量，避免
        # 一笔委托拆成多次成交时重复收取最低佣金。
        by_order: dict[str, list[BrokerTrade]] = {}
        for trade in trades:
            if trade.client_order_id:
                by_order.setdefault(trade.client_order_id, []).append(trade)
        for group in by_order.values():
            cumulative_gross = 0.0
            cumulative_fee = 0.0
            for trade in sorted(group, key=lambda item: (item.at, item.traded_id)):
                cumulative_gross += trade.price * trade.qty
                total_fee = self._fees.fee_for(Side(trade.side), cumulative_gross)
                incremental_fee = to_cents(max(0.0, total_fee - cumulative_fee))
                cumulative_fee = total_fee
                if trade.traded_id in seen_trade_ids:
                    continue
                fills.append(FillEvent(
                    client_order_id=trade.client_order_id,
                    symbol=trade.symbol,
                    side=trade.side,
                    qty=trade.qty,
                    price=trade.price,
                    fee=incremental_fee,
                    filled_at=trade.at,
                    broker_order_id=trade.broker_order_id,
                    broker_trade_id=trade.traded_id,
                ))
                new_trade_ids.append(trade.traded_id)

        for order in orders:
            if not order.client_order_id:
                continue
            remaining = max(0, order.ordered_qty - order.traded_qty)
            terminal = order.status in TERMINAL_STATUSES
            if order.status == 56 or (terminal and remaining == 0):
                state = "filled"
            elif order.status == 57:
                state = "rejected"
            elif terminal:
                state = "cancelled"
            elif order.traded_qty > 0:
                state = "partially_filled"
            elif order.status in {48, 49, 50, 51}:
                state = "accepted"
            else:
                state = "unknown"
            statuses.append(OrderStatusEvent(
                client_order_id=order.client_order_id,
                broker_order_id=order.broker_order_id,
                symbol=order.symbol,
                side=order.side,
                ordered_qty=order.ordered_qty,
                filled_qty=order.traded_qty,
                remaining_qty=remaining,
                status=state,
                at=now,
                reason=order.status_msg or STATUS_NAMES.get(order.status, str(order.status)),
            ))
            if not terminal or order.client_order_id in closed_order_ids:
                continue
            closed.append(order.client_order_id)
            if remaining:
                label = order.status_msg or STATUS_NAMES.get(order.status, str(order.status))
                rejects.append(RejectEvent(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    reason=(f"{label}: 委托 {order.ordered_qty} 股，"
                            f"成交 {order.traded_qty} 股"),
                    at=now,
                    side=order.side,
                    unfilled_qty=remaining,
                ))

        return TrackerOutput(
            tuple(fills), tuple(rejects), tuple(statuses),
            tuple(new_trade_ids), tuple(closed),
        )
