"""上线验证单：手工下一笔最小真实委托，把 QMT 那一段链路当场验通。

为什么需要它：影子盘从不下单（历史委托数 0），所以观察期再长也证明不了下单链路。
而策略约每 3.6 个交易日才出手一次，等自然信号来验管道，排查节奏太慢——
信号没来只能干等，出了问题还分不清是管道还是策略。
于是照交易系统上线的通行做法，先用一笔最小单把管道打通。

**核心必须处于停止状态。** 核心启动时会拿本地推演的持仓与券商快照对账
（``LiveRuntime._assert_reconciled``），一笔它不知道的手工持仓会直接判不一致、
触发停机。所以流程是：停核心 → 验证单买入 → 确认成交 → 卖出平掉 →
账户回到空仓 → 再启动核心。

用法（每一步都要人在场看着）：

    uv run python scripts/commissioning_order.py --symbol 510300.SH --side buy \\
        --qty 100 --limit 4.10 --confirm 510300.SH
    uv run python scripts/commissioning_order.py --symbol 510300.SH --side sell \\
        --qty 100 --limit 3.90 --confirm 510300.SH
"""

from __future__ import annotations

import argparse
import json
import sys
import time as clock
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# 验证单只为打通管道，不是为了赚钱：卡死在"赔得起"的量级上。
MAX_QTY = 200
MAX_NOTIONAL = 3000.0
CORE_BEAT_MAX_AGE = 30.0
AGENT_BEAT_MAX_AGE = 10.0
POLL_SECONDS = 0.5


def _fail(message: str) -> int:
    print(f"[拒绝] {message}", file=sys.stderr)
    return 2


def _preflight(config, transport, args) -> str | None:
    """返回拒绝原因；None 表示可以下单。逐条都是能把真钱打出去的前提。"""
    safety = config.safety
    if safety.mode != "live" or not safety.orders_enabled:
        return (f"三道写保护未全开（mode={safety.mode!r}、"
                f"orders_enabled={safety.orders_enabled}）")
    if not safety.protected_account_ids:
        return "protected_account_ids 为空，账号白名单没配"

    if args.qty > MAX_QTY:
        return f"数量 {args.qty} 超过验证单上限 {MAX_QTY} 股"
    notional = args.qty * args.limit
    if notional > MAX_NOTIONAL:
        return f"金额 {notional:.0f} 元超过验证单上限 {MAX_NOTIONAL:.0f} 元"

    # 核心在跑就不能插手：它按自己推演的持仓与券商对账，多出一笔会判不一致而停机
    beat = transport.get_key(f"{config.streams.heartbeat}:core")
    if beat is not None:
        age = (datetime.now() - _beat_at(beat)).total_seconds()
        if age < CORE_BEAT_MAX_AGE:
            return (f"实盘核心还在跑（心跳 {age:.1f} 秒前）。验证单必须在核心停止时做，"
                    "否则它的持仓对账会失败并触发停机")

    agent = transport.get_key(f"{config.streams.heartbeat}:agent")
    if agent is None:
        return "取不到执行代理心跳，Windows 侧可能没起来"
    age = (datetime.now() - _beat_at(agent)).total_seconds()
    if age > AGENT_BEAT_MAX_AGE:
        return f"执行代理心跳已过期 {age:.1f} 秒"

    if transport.get_key("control:kill") is not None:
        return "停机开关处于生效状态，先确认原因再解除"
    return None


def _beat_at(raw: str) -> datetime:
    return datetime.fromisoformat(json.loads(raw)["at"])


def _expected_fee(config, side: str, qty: int, price: float) -> float:
    fees = config.fees
    gross = qty * price
    fee = max(gross * fees.commission_rate, fees.min_commission)
    fee += gross * fees.transfer_fee_rate
    if side == "sell":
        fee += gross * fees.stamp_duty_rate
    return fee


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", required=True, choices=("buy", "sell"))
    parser.add_argument("--qty", type=int, required=True, help=f"≤ {MAX_QTY} 股")
    parser.add_argument("--limit", type=float, required=True, help="限价，不接受市价单")
    parser.add_argument("--confirm", required=True,
                        help="重复输入一遍标的代码，确认不是手滑")
    parser.add_argument("--config", default=None)
    parser.add_argument("--wait", type=float, default=60.0, help="等回报的秒数")
    parser.add_argument("--record", type=Path, default=None, help="把结果追加到这个文件")
    args = parser.parse_args()

    if args.confirm != args.symbol:
        return _fail(f"--confirm {args.confirm!r} 与 --symbol {args.symbol!r} 不一致")

    from qsys.live.config import load_deploy_config
    from qsys.live.messages import FillEvent, OrderCommand, OrderStatusEvent, RejectEvent
    from qsys.live.redis_transport import RedisTransport

    config = load_deploy_config(args.config)
    transport = RedisTransport.from_config(config)

    refusal = _preflight(config, transport, args)
    if refusal:
        return _fail(refusal)

    # 先建自己的消费组再下单：消费组之间互不影响，这里读过的消息核心之后照样收得到；
    # 建在下单之前，才不会漏掉代理毫秒级的回报。
    report_streams = (
        (config.streams.fills, FillEvent),
        (config.streams.rejects, RejectEvent),
        (config.streams.order_status, OrderStatusEvent),
    )
    group = f"commissioning-{uuid.uuid4().hex[:8]}"
    for stream, _ in report_streams:
        transport.ensure_group(stream, group, start_id="$")

    order_id = f"commission-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    command = OrderCommand(
        client_order_id=order_id, symbol=args.symbol, side=args.side,
        qty=args.qty, order_type="limit", limit_price=args.limit,
        created_at=datetime.now(),
        expires_at=datetime.now() + timedelta(minutes=5),
        strategy_id="commissioning",
    )
    notional = args.qty * args.limit
    print(f"下单：{args.side} {args.symbol} {args.qty} 股 @ {args.limit}"
          f"（约 {notional:.0f} 元），委托号 {order_id}")
    transport.append(config.streams.orders, command.to_payload())

    deadline = clock.monotonic() + args.wait
    fills: list = []
    while clock.monotonic() < deadline:
        for stream, kind in report_streams:
            for message in transport.read_group(stream, group, "cli", count=50):
                try:
                    event = kind.from_payload(message.payload)
                except Exception:
                    continue
                if event.client_order_id != order_id:
                    continue
                if isinstance(event, FillEvent):
                    fills.append(event)
                    print(f"  成交 {event.qty} 股 @ {event.price}，费用 {event.fee:.2f}")
                elif isinstance(event, RejectEvent):
                    print(f"  拒单：{event.reason}")
                    return 1
                else:
                    print(f"  状态 {event.status}"
                          f"（已成 {event.filled_qty}/{event.ordered_qty}）")
        if sum(f.qty for f in fills) >= args.qty:
            break
        clock.sleep(POLL_SECONDS)

    filled = sum(f.qty for f in fills)
    if not filled:
        print("没等到成交。限价可能挂不上——先撤单再看券商端，不要重复下单。")
        return 1

    paid = sum(f.fee for f in fills)
    avg = sum(f.qty * f.price for f in fills) / filled
    expect = _expected_fee(config, args.side, filled, avg)
    print(f"\n成交 {filled} 股，均价 {avg:.4f}")
    print(f"实收费用 {paid:.2f} 元；按配置费率应为 {expect:.2f} 元"
          f"（差 {paid - expect:+.2f}）")
    if abs(paid - expect) > 0.01:
        print("  !! 与费率模型对不上，核对券商费率后再改 [fees]，不要改模型迁就")

    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        with args.record.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "at": datetime.now().isoformat(), "order_id": order_id,
                "symbol": args.symbol, "side": args.side, "qty": filled,
                "avg_price": round(avg, 4), "fee_paid": round(paid, 2),
                "fee_expected": round(expect, 2),
            }, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
