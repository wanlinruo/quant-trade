"""故障演练用的桩代理：扮演 Windows 执行代理，按剧本回吐券商回报。

上线清单要求演练部分成交、撤单、废单、重复消息，而这些回报只有券商会产生。
在拿到券商仿真账户之前，用它顶上——**要验的本来就是核心的处理逻辑**，
券商那端是 QMT 的事。

两种用法，同一套代码：

- 当库用（``respond_once``）：进程内驱动，确定性强，用于断言式演练；
- 当进程用（``python scripts/drill_agent.py --config … --scenario …``）：
  给一个真起的核心实例当对手方，同时发心跳与账户快照。

**只接隔离实例的流**。剧本里的 broker_order_id 一律带 DRILL- 前缀，
真代理不会消费这些流，真账户也不在这条链路上。
"""

from __future__ import annotations

import argparse
import time
import uuid
from collections.abc import Iterator
from datetime import datetime

import structlog

from qsys.live.config import StreamConfig, load_deploy_config
from qsys.live.messages import (
    AccountSnapshot,
    FillEvent,
    Heartbeat,
    OrderCommand,
    OrderStatusEvent,
    RejectEvent,
)
from qsys.live.transport import Transport

log = structlog.get_logger("drill-agent")

SCENARIOS = ("normal", "partial", "cancel", "reject", "duplicate")
SCENARIO_LABELS = {
    "normal": "全部成交",
    "partial": "部分成交后停住",
    "cancel": "受理后被撤",
    "reject": "废单",
    "duplicate": "同一笔成交回报重复投递",
}
GROUP = "drill-agent"


def _fill(order: OrderCommand, qty: int, price: float, trade_id: str) -> dict[str, str]:
    return FillEvent(
        client_order_id=order.client_order_id, symbol=order.symbol, side=order.side,
        qty=qty, price=price, fee=max(5.0, qty * price * 0.000075),
        filled_at=datetime.now(),
        broker_order_id=f"DRILL-{order.client_order_id}",
        broker_trade_id=trade_id,
    ).to_payload()


def _status(order: OrderCommand, filled: int, status: str, reason: str = "") -> dict[str, str]:
    return OrderStatusEvent(
        client_order_id=order.client_order_id,
        broker_order_id=f"DRILL-{order.client_order_id}",
        symbol=order.symbol, side=order.side, ordered_qty=order.qty,
        filled_qty=filled, remaining_qty=order.qty - filled,
        status=status, at=datetime.now(), reason=reason,
    ).to_payload()


def script(order: OrderCommand, scenario: str, price: float) -> Iterator[tuple[str, dict[str, str]]]:
    """一条指令对应的回报序列，产出 (流名, 载荷)。流名用 StreamConfig 的字段名。"""
    if scenario == "reject":
        yield "rejects", RejectEvent(
            client_order_id=order.client_order_id, symbol=order.symbol,
            reason="DRILL 废单演练", at=datetime.now(), side=order.side,
            unfilled_qty=order.qty,
        ).to_payload()
        return

    yield "order_status", _status(order, 0, "accepted")

    if scenario == "cancel":
        yield "order_status", _status(order, 0, "cancelled", "DRILL 撤单演练")
        return

    if scenario == "partial":
        half = max(1, order.qty // 2 // 100 * 100) or 100
        half = min(half, order.qty - 100) if order.qty > 100 else order.qty
        yield "fills", _fill(order, half, price, f"DRILL-T-{uuid.uuid4().hex[:8]}")
        yield "order_status", _status(order, half, "partially_filled")
        return

    trade_id = f"DRILL-T-{uuid.uuid4().hex[:8]}"
    yield "fills", _fill(order, order.qty, price, trade_id)
    yield "order_status", _status(order, order.qty, "filled")
    if scenario == "duplicate":
        # 同一个 broker_trade_id 再投一次：网络重传的样子
        yield "fills", _fill(order, order.qty, price, trade_id)


def respond_once(transport: Transport, streams: StreamConfig, scenario: str, *,
                 price: float = 10.0, count: int = 100) -> list[OrderCommand]:
    """消费当前待处理的下单指令，按剧本回吐回报。返回处理过的指令。"""
    transport.ensure_group(streams.orders, GROUP, start_id="0-0")
    messages = transport.read_group(streams.orders, GROUP, "drill-1", count=count)
    handled: list[OrderCommand] = []
    for message in messages:
        order = OrderCommand.from_payload(message.payload)
        for field, payload in script(order, scenario, price):
            transport.append(getattr(streams, field), payload)
        transport.ack(streams.orders, GROUP, message.id)
        handled.append(order)
        log.info("drill_agent.responded", client_order_id=order.client_order_id,
                 scenario=scenario, qty=order.qty)
    return handled


def publish_presence(transport: Transport, streams: StreamConfig,
                     cash: float = 100_000.0) -> None:
    """心跳与账户快照：核心的盘前门禁要求两者都新鲜。"""
    now = datetime.now()
    for source in ("agent", "market"):
        transport.set_key(
            f"{streams.heartbeat}:{source}",
            Heartbeat(source=source, at=now).model_dump_json(),
        )
    transport.set_key(streams.account, AccountSnapshot(
        cash=cash, total_asset=cash, market_value=0.0, positions={}, at=now,
    ).model_dump_json())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="演练配置（隔离的流前缀）")
    parser.add_argument("--scenario", default="normal", choices=SCENARIOS)
    parser.add_argument("--price", type=float, default=10.0)
    parser.add_argument("--seconds", type=float, default=0.0, help="0 表示一直跑")
    args = parser.parse_args()

    config = load_deploy_config(args.config)
    if not config.streams.orders.startswith("drill:"):
        raise SystemExit("拒绝启动：流前缀不是 drill:，这个桩代理只接隔离实例")

    from qsys.live.redis_transport import RedisTransport

    transport = RedisTransport.from_config(config)
    log.info("drill_agent.started", scenario=args.scenario,
             orders=config.streams.orders)
    deadline = time.monotonic() + args.seconds if args.seconds else None
    while deadline is None or time.monotonic() < deadline:
        publish_presence(transport, config.streams)
        respond_once(transport, config.streams, args.scenario, price=args.price)
        time.sleep(1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
