"""Arm/status/disarm the bounded first-order maintenance window.

Arming does not place an order. It only allows the Windows agent to remain in
LIVE mode for at most ten minutes while the strategy core is deliberately
stopped. During that window the agent accepts only the fixed commissioning
instrument and rejects normal strategy orders.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time

from qsys.live.commissioning import (
    COMMISSIONING_KEY,
    COMMISSIONING_SYMBOL,
    CommissioningLease,
    read_commissioning_lease,
)
from qsys.live.config import load_deploy_config
from qsys.live.messages import AccountSnapshot, Heartbeat, MarketSchedule
from qsys.live.redis_transport import RedisTransport

MARKET_SCHEDULE_MAX_AGE = 45.0
CORE_BEAT_MAX_AGE = 10.0


def _fresh(at: datetime, now: datetime, seconds: float) -> bool:
    return -1 <= (now - at).total_seconds() <= seconds


def _within_safe_window(now: datetime) -> bool:
    wall = now.time()
    return time(9, 35) <= wall <= time(11, 15) or time(13, 5) <= wall <= time(14, 40)


def _arm_refusal(cfg, transport, now: datetime) -> str | None:
    if not _within_safe_window(now):
        return "只允许在交易日 09:35–11:15 或 13:05–14:40 武装验证窗口"
    if transport.get_key("control:kill") is not None:
        return "远端停机开关仍生效"

    raw_schedule = transport.get_key(cfg.streams.market_schedule)
    schedule = MarketSchedule.model_validate_json(raw_schedule) if raw_schedule else None
    if schedule is None or not _fresh(
        schedule.checked_at, now, MARKET_SCHEDULE_MAX_AGE
    ):
        return f"市场时段快照缺失或超过 {MARKET_SCHEDULE_MAX_AGE:.0f} 秒"
    if not schedule.is_trading_day or not schedule.is_open:
        return "当前不是交易时段"

    raw_account = transport.get_key(cfg.streams.account)
    account = AccountSnapshot.model_validate_json(raw_account) if raw_account else None
    if account is None or not _fresh(account.at, now, 5):
        return "券商账户快照缺失或超过 5 秒"
    if account.active_order_count is None:
        return "券商账户活动委托数未知，需先升级并确认 Windows agent"
    if account.active_order_count != 0:
        return f"账户存在 {account.active_order_count} 笔活动委托，禁止武装"
    positions = {k: v.total_qty for k, v in account.positions.items() if v.total_qty}
    if positions:
        return f"账户不是空仓，禁止武装：{sorted(positions)}"

    raw_agent = transport.get_key(f"{cfg.streams.heartbeat}:agent")
    agent = Heartbeat.model_validate_json(raw_agent) if raw_agent else None
    if agent is None or not _fresh(agent.at, now, 10):
        return "Windows agent 心跳缺失或超过 10 秒"
    if agent.account_protected is not True or agent.local_kill_engaged is not False:
        return "Windows 账号白名单或本地停机闸状态不满足"

    # Establish the lease before stopping the core.  Requiring the opposite
    # creates an impossible window: the agent trips after 10 seconds without a
    # core, while the old gate waited 30 seconds for that heartbeat to age out.
    raw_core = transport.get_key(f"{cfg.streams.heartbeat}:core")
    core = Heartbeat.model_validate_json(raw_core) if raw_core else None
    if core is None or not _fresh(core.at, now, CORE_BEAT_MAX_AGE):
        return "实盘核心心跳缺失或超过 10 秒，禁止开始切换"
    if core.event_timed_out or core.activity != "idle":
        return "实盘核心不是健康空闲状态，禁止开始切换"
    return None


def _disarm_refusal(cfg, transport, now: datetime) -> str | None:
    """Do not remove the lease while it is the live agent's heartbeat exemption."""
    raw_agent = transport.get_key(f"{cfg.streams.heartbeat}:agent")
    agent = Heartbeat.model_validate_json(raw_agent) if raw_agent else None
    if agent is None or not _fresh(agent.at, now, 10):
        return "Windows agent 心跳缺失或超过 10 秒，无法确认安全状态"
    if agent.trading_mode != "live":
        return None
    raw_core = transport.get_key(f"{cfg.streams.heartbeat}:core")
    core = Heartbeat.model_validate_json(raw_core) if raw_core else None
    if core is None or not _fresh(core.at, now, 10) or core.event_timed_out:
        return "agent 仍为 live 且核心未健康接管；先恢复核心或把 agent 切回 shadow"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("arm", "status", "disarm"))
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    cfg = load_deploy_config(args.config)
    transport = RedisTransport.from_config(cfg)
    now = datetime.now()

    if args.action == "status":
        lease = read_commissioning_lease(transport, now=now)
        if lease is None:
            print("commissioning lease: inactive")
            return 1
        print(json.dumps(lease.model_dump(mode="json"), ensure_ascii=False))
        return 0

    if args.action == "disarm":
        if args.confirm != "DISARM_COMMISSIONING":
            print("[拒绝] --confirm 必须为 DISARM_COMMISSIONING", file=sys.stderr)
            return 2
        refusal = _disarm_refusal(cfg, transport, now)
        if refusal:
            print(f"[拒绝] {refusal}", file=sys.stderr)
            return 2
        transport.delete_key(COMMISSIONING_KEY)
        print("commissioning lease: disarmed")
        return 0

    if args.confirm != f"ARM_{COMMISSIONING_SYMBOL}":
        print(f"[拒绝] --confirm 必须为 ARM_{COMMISSIONING_SYMBOL}", file=sys.stderr)
        return 2
    refusal = _arm_refusal(cfg, transport, now)
    if refusal:
        print(f"[拒绝] {refusal}", file=sys.stderr)
        return 2
    lease = CommissioningLease.arm(now=now)
    transport.set_key(COMMISSIONING_KEY, lease.model_dump_json())
    print(json.dumps(lease.model_dump(mode="json"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
