"""Send one tightly bounded real order for first-link commissioning.

The live core must be stopped. A short-lived commissioning lease must be armed
first; while active, the Windows agent rejects normal strategy orders and only
accepts a one-lot buy or a reducing sell in ``511380.SH``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time as clock
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

from qsys.live.commissioning import (
    COMMISSIONING_MAX_NOTIONAL,
    COMMISSIONING_QTY,
    COMMISSIONING_SYMBOL,
    commissioning_qty_allowed,
    new_commissioning_order_id,
    read_commissioning_lease,
)

CORE_BEAT_MAX_AGE = 10.0
AGENT_BEAT_MAX_AGE = 10.0
SNAPSHOT_MAX_AGE = 5.0
MARKET_SCHEDULE_MAX_AGE = 45.0
POLL_SECONDS = 0.5
CANCEL_WAIT_SECONDS = 15.0


def _fail(message: str) -> int:
    print(f"[拒绝] {message}", file=sys.stderr)
    return 2


def _fresh(at: datetime, now: datetime, seconds: float) -> bool:
    return -1 <= (now - at).total_seconds() <= seconds


def _within_safe_window(now: datetime) -> bool:
    wall = now.time()
    return time(9, 35) <= wall <= time(11, 15) or time(13, 5) <= wall <= time(14, 40)


def _preflight(config, transport, args, *, now: datetime | None = None) -> str | None:
    """Return a refusal reason; every check fails closed before any order."""
    from qsys.live.messages import AccountSnapshot, Heartbeat, MarketSchedule

    now = now or datetime.now()
    safety = config.safety
    if safety.mode != "live" or not safety.orders_enabled:
        return (f"三道写保护未全开（mode={safety.mode!r}、"
                f"orders_enabled={safety.orders_enabled}）")
    if not safety.protected_account_ids:
        return "protected_account_ids 为空，账号白名单没配"
    if args.symbol != COMMISSIONING_SYMBOL or not commissioning_qty_allowed(
        args.side, args.qty
    ):
        return (
            f"验证买单固定为 {COMMISSIONING_SYMBOL} {COMMISSIONING_QTY} 份；"
            "卖单只允许平掉不超过 100 份的实际残仓"
        )
    if args.limit <= 0:
        return "限价必须大于 0"
    notional = args.qty * args.limit
    if notional > COMMISSIONING_MAX_NOTIONAL:
        return (f"金额 {notional:.0f} 元超过验证单上限 "
                f"{COMMISSIONING_MAX_NOTIONAL:.0f} 元")
    if not _within_safe_window(now):
        return "只允许在交易日 09:35–11:15 或 13:05–14:40 下验证单"
    if read_commissioning_lease(transport, now=now) is None:
        return "验证窗口未武装或已过期"

    raw_core = transport.get_key(f"{config.streams.heartbeat}:core")
    if raw_core:
        core = Heartbeat.model_validate_json(raw_core)
        age = (now - core.at).total_seconds()
        if _fresh(core.at, now, CORE_BEAT_MAX_AGE):
            return (f"实盘核心还在跑（心跳 {age:.1f} 秒前）。验证单必须在核心停止时做，"
                    "否则它的持仓对账会失败并触发停机")

    raw_agent = transport.get_key(f"{config.streams.heartbeat}:agent")
    agent = Heartbeat.model_validate_json(raw_agent) if raw_agent else None
    if agent is None:
        return "取不到执行代理心跳，Windows 侧可能没起来"
    age = (now - agent.at).total_seconds()
    if not _fresh(agent.at, now, AGENT_BEAT_MAX_AGE):
        return f"执行代理心跳已过期 {age:.1f} 秒"
    if agent.trading_mode != "live":
        return "Windows 执行代理尚未声明 trading_mode=live"
    if agent.orders_enabled is not True:
        return "Windows 执行代理尚未声明 orders_enabled=true"
    if agent.account_protected is not True:
        return "Windows 执行代理当前账号未确认在白名单"
    if agent.local_kill_engaged is not False:
        return "Windows 执行代理本地停机闸状态未知或仍生效"
    if agent.commissioning_lease_active is not True:
        return "Windows 执行代理尚未确认验证窗口"

    raw_schedule = transport.get_key(config.streams.market_schedule)
    schedule = MarketSchedule.model_validate_json(raw_schedule) if raw_schedule else None
    if schedule is None or not _fresh(
        schedule.checked_at, now, MARKET_SCHEDULE_MAX_AGE
    ):
        return f"市场时段快照缺失或超过 {MARKET_SCHEDULE_MAX_AGE:.0f} 秒"
    if not schedule.is_trading_day or not schedule.is_open:
        return "当前不是交易时段"

    raw_account = transport.get_key(config.streams.account)
    account = AccountSnapshot.model_validate_json(raw_account) if raw_account else None
    if account is None or not _fresh(account.at, now, SNAPSHOT_MAX_AGE):
        return "券商账户快照缺失或超过 5 秒"
    if account.active_order_count is None:
        return "券商账户活动委托数未知，拒绝下单"
    if account.active_order_count != 0:
        return f"账户存在 {account.active_order_count} 笔活动委托，拒绝下单"
    positions = {k: v for k, v in account.positions.items() if v.total_qty}
    if args.side == "buy" and positions:
        return f"买入验证前账户必须空仓，当前持仓：{sorted(positions)}"
    if args.side == "sell":
        target = positions.get(args.symbol)
        others = sorted(k for k in positions if k != args.symbol)
        if others:
            return f"卖出验证时存在其他持仓：{others}"
        if target is None or target.total_qty != args.qty or target.available_qty != args.qty:
            return "卖出数量必须精确等于目标实际持仓和可用数量"
    if transport.get_key("control:kill") is not None:
        return "停机开关处于生效状态，先确认原因再解除"
    return None


def _write_record(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _fill_identity(event) -> str:
    return event.broker_trade_id or (
        f"{event.filled_at.isoformat()}:{event.qty}:{event.price}:{event.fee}"
    )


def _quote_refusal(args, quote) -> str | None:
    if quote is None or quote.suspended or quote.price <= 0:
        return "511380.SH 行情缺失、停牌或最新价无效"
    if abs(args.limit * 1000 - round(args.limit * 1000)) > 1e-6:
        return "ETF 限价必须按 0.001 元价格档位输入"
    reference = quote.ask1 if args.side == "buy" else quote.bid1
    if reference <= 0:
        return "买一/卖一缺失，禁止验证单"
    if args.side == "buy" and args.limit > reference * 1.005:
        return f"买入限价 {args.limit} 高于卖一 {reference} 的 0.5% 保护带"
    if args.side == "sell" and args.limit < reference * 0.995:
        return f"卖出限价 {args.limit} 低于买一 {reference} 的 0.5% 保护带"
    return None


def _wait_account_after(transport, config, *, after: datetime, args, seconds: float = 10.0):
    from qsys.live.messages import AccountSnapshot

    deadline = clock.monotonic() + seconds
    latest = None
    while clock.monotonic() < deadline:
        raw = transport.get_key(config.streams.account)
        latest = AccountSnapshot.model_validate_json(raw) if raw else None
        if latest is not None and latest.at >= after:
            position = latest.positions.get(args.symbol)
            qty = position.total_qty if position is not None else 0
            if (args.side == "buy" and qty >= args.qty) or (args.side == "sell" and qty == 0):
                return latest
        clock.sleep(POLL_SECONDS)
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", required=True, choices=("buy", "sell"))
    parser.add_argument("--qty", type=int, required=True)
    parser.add_argument("--limit", type=float, required=True, help="实时限价，不接受市价单")
    parser.add_argument("--confirm", required=True, help="重复输入标的代码")
    parser.add_argument("--config", default=None)
    parser.add_argument("--wait", type=float, default=30.0, help="等完整成交的秒数")
    parser.add_argument("--record", type=Path, default=None)
    args = parser.parse_args()
    if args.confirm != args.symbol:
        return _fail(f"--confirm {args.confirm!r} 与 --symbol {args.symbol!r} 不一致")

    from qsys.live.config import load_deploy_config
    from qsys.live.messages import (
        AccountSnapshot, ControlCommand, FillEvent, OrderCommand, OrderStatusEvent,
        RejectEvent,
    )
    from qsys.live.redis_transport import RedisTransport
    from qsys.paper.quotes import QuoteClient

    config = load_deploy_config(args.config)
    transport = RedisTransport.from_config(config)
    refusal = _preflight(config, transport, args)
    if refusal:
        return _fail(refusal)
    quotes = QuoteClient(
        transport, config.streams, consumer=f"commissioning-{uuid.uuid4().hex[:8]}",
        timeout=5.0,
    ).request([args.symbol])
    quote = quotes.get(args.symbol)
    refusal = _quote_refusal(args, quote)
    if refusal:
        return _fail(refusal)
    print(
        f"实时行情：最新 {quote.price:.3f}，买一 {quote.bid1:.3f}，"
        f"卖一 {quote.ask1:.3f}"
    )
    raw_before = transport.get_key(config.streams.account)
    account_before = AccountSnapshot.model_validate_json(raw_before)

    report_streams = (
        (config.streams.fills, FillEvent),
        (config.streams.rejects, RejectEvent),
        (config.streams.order_status, OrderStatusEvent),
    )
    group = f"commissioning-{uuid.uuid4().hex[:8]}"
    for stream, _ in report_streams:
        transport.ensure_group(stream, group, start_id="$")

    order_id = new_commissioning_order_id()
    command = OrderCommand(
        client_order_id=order_id, symbol=args.symbol, side=args.side,
        qty=args.qty, order_type="limit", limit_price=args.limit,
        created_at=datetime.now(), expires_at=datetime.now() + timedelta(minutes=2),
        strategy_id="commissioning",
    )
    print(f"下单：{args.side} {args.symbol} {args.qty} 份 @ {args.limit}，委托号 {order_id}")
    transport.append(config.streams.orders, command.to_payload())

    fills: dict[str, object] = {}
    terminal_status = ""
    broker_order_id = ""
    rejected = ""

    def poll_once() -> None:
        nonlocal terminal_status, broker_order_id, rejected
        for stream, kind in report_streams:
            for message in transport.read_group(stream, group, "cli", count=50):
                try:
                    event = kind.from_payload(message.payload)
                except Exception:
                    continue
                if event.client_order_id != order_id:
                    continue
                if isinstance(event, FillEvent):
                    identity = _fill_identity(event)
                    if identity not in fills:
                        fills[identity] = event
                        print(
                            f"  成交 {event.qty} 份 @ {event.price}，"
                            f"模型费用 {event.fee:.2f}"
                        )
                elif isinstance(event, RejectEvent):
                    rejected = event.reason
                    print(f"  拒单：{event.reason}")
                else:
                    terminal_status = event.status
                    broker_order_id = event.broker_order_id
                    print(f"  状态 {event.status}（已成 {event.filled_qty}/{event.ordered_qty}）")

    deadline = clock.monotonic() + args.wait
    while clock.monotonic() < deadline and not rejected:
        poll_once()
        if sum(f.qty for f in fills.values()) >= args.qty:
            break
        clock.sleep(POLL_SECONDS)

    filled = sum(f.qty for f in fills.values())
    if not rejected and filled < args.qty:
        print(f"仅成交 {filled}/{args.qty}，发送 cancel_all 并等待撤单终态。")
        transport.append(config.streams.control, ControlCommand(
            action="cancel_all", at=datetime.now(), reason=f"commissioning timeout {order_id}",
        ).to_payload())
        deadline = clock.monotonic() + CANCEL_WAIT_SECONDS
        while clock.monotonic() < deadline:
            poll_once()
            filled = sum(f.qty for f in fills.values())
            if filled >= args.qty or terminal_status in {"cancelled", "rejected"}:
                break
            clock.sleep(POLL_SECONDS)

    filled = sum(f.qty for f in fills.values())
    if rejected or filled != args.qty:
        _write_record(args.record, {
            "at": datetime.now().isoformat(), "order_id": order_id,
            "broker_order_id": broker_order_id, "symbol": args.symbol,
            "side": args.side, "requested_qty": args.qty, "filled_qty": filled,
            "terminal_status": terminal_status or "unconfirmed", "reject": rejected,
            "outcome": "rejected" if rejected else "incomplete_cancel_requested",
        })
        print("验证单未完整成交；必须先在券商端确认残单和持仓，禁止重复下单。")
        return 1

    modeled_fee = sum(f.fee for f in fills.values())
    avg = sum(f.qty * f.price for f in fills.values()) / filled
    account_after = _wait_account_after(
        transport, config, after=command.created_at, args=args,
    )
    position = account_after.positions.get(args.symbol) if account_after else None
    position_qty = position.total_qty if position is not None else 0
    account_ok = (
        account_after is not None
        and ((args.side == "buy" and position_qty >= args.qty)
             or (args.side == "sell" and position_qty == 0))
    )
    gross = avg * filled
    cash_delta = account_after.cash - account_before.cash if account_after else None
    cash_inferred_fee = None
    if cash_delta is not None:
        cash_inferred_fee = (
            -cash_delta - gross if args.side == "buy" else gross - cash_delta
        )
        cash_inferred_fee = round(cash_inferred_fee, 2)
    print(f"\n完整成交 {filled} 份，均价 {avg:.4f}，模型费用 {modeled_fee:.2f} 元")
    print(
        f"账户快照持仓 {position_qty} 份；"
        f"按券商现金变化反推实收费用 {cash_inferred_fee!r} 元"
    )
    if not account_ok:
        print("成交回报与账户持仓快照尚未收敛，停止后续步骤并人工核对。")
    _write_record(args.record, {
        "at": datetime.now().isoformat(), "order_id": order_id,
        "broker_order_id": broker_order_id, "symbol": args.symbol,
        "side": args.side, "requested_qty": args.qty, "filled_qty": filled,
        "avg_price": round(avg, 4), "modeled_fee": round(modeled_fee, 2),
        "cash_before": account_before.cash,
        "cash_after": account_after.cash if account_after else None,
        "cash_inferred_fee": cash_inferred_fee,
        "position_after": position_qty, "fee_scope": "bond_etf_cash_inferred",
        "terminal_status": terminal_status,
        "outcome": "filled_and_account_reconciled" if account_ok else "account_unconfirmed",
    })
    return 0 if account_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
