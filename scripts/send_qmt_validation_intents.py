#!/usr/bin/env python3
"""#601 F2：在隔离验证命名空间发布固定合成订单指令，并汇总执行链路回执。

只接受 ``component-validation:`` 命名空间（ValidationTransport），绝不写生产
``cmd:orders``。指令形状与 Live Core 的 OrderCommand 相同（schema v2、22 字节
委托号、显式有效期）。证券、数量、价格固定；执行器在 DRY_RUN 下不会下单。

用法:
    python scripts/send_qmt_validation_intents.py --namespace component-validation:qmt-f2 \
        --strategy-id structural --scenario accept --scenario board_reject --wait 20
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import sys
import time

from qsys.core.order_ids import live_order_id
from qsys.live.messages import DispatchStatusEvent, Heartbeat, OrderCommand, RejectEvent
from qsys.live.validation_transport import ValidationTransport

ORDERS, REJECTS, DISPATCH = 'cmd:orders', 'evt:rejects', 'evt:dispatch_status'
HEARTBEAT_CORE = 'state:heartbeat:core'
SCENARIOS = {
    # 合规买单：执行器 DRY_RUN 回 accepted/dry_run → transport_received
    'accept': dict(symbol='510300.SH', side='buy', qty=100, order_type='limit', limit_price=1.0),
    # 科创板买入：执行器 market_blocked → 发送前拒单
    'board_reject': dict(symbol='688001.SH', side='buy', qty=100, order_type='limit', limit_price=10.0),
    # 市价卖出：报价类型未启用时 market_price_type_unverified → 拒单
    'market_sell': dict(symbol='510300.SH', side='sell', qty=100, order_type='market', limit_price=None),
    # 同一指令重投两次：第二次只重放账本事实，不再发给执行器
    'duplicate': dict(symbol='510300.SH', side='buy', qty=100, order_type='limit', limit_price=1.0),
    # 同一委托号、不同内容：账本冲突必须失败关闭，不覆盖原意图
    'conflict': dict(symbol='510300.SH', side='buy', qty=100, order_type='limit', limit_price=1.0),
}
KILL = 'control:kill'


def submit(transport, *, scenarios: list[str], strategy_id: str, now: datetime,
           ttl_seconds: float = 60.0) -> list[dict]:
    if not isinstance(transport, ValidationTransport):
        raise ValueError('validation intents require an isolated ValidationTransport')
    sent = []
    for index, name in enumerate(scenarios, 1):
        spec = SCENARIOS[name]
        cid = live_order_id(strategy_id, now.date(), f'f2:{name}:{now.isoformat()}', index)
        command = OrderCommand(schema_version=2, session_id=now.date().isoformat(),
                               strategy_id=strategy_id, client_order_id=cid, created_at=now,
                               expires_at=now + timedelta(seconds=ttl_seconds), **spec)
        transport.append(ORDERS, command.to_payload())
        if name == 'duplicate':
            transport.append(ORDERS, command.to_payload())
        elif name == 'conflict':
            transport.append(ORDERS, command.model_copy(update={'qty': 200}).to_payload())
        sent.append({'scenario': name, 'client_order_id': cid})
    return sent


def set_kill(transport, engaged: bool) -> None:
    """只在验证命名空间设置/清除远程 KILL 键（演练急停）。"""
    if not isinstance(transport, ValidationTransport):
        raise ValueError('validation kill drill requires an isolated ValidationTransport')
    if engaged:
        transport.set_key(KILL, 'f4 drill: validation namespace kill')
    else:
        transport.delete_key(KILL)


def run_core_heartbeat(transport, *, seconds: int, sleep=time.sleep, clock=datetime.now) -> None:
    """在验证命名空间模拟 Live Core 心跳（每秒一次），让 LIVE 姿态的验证网关不因
    缺少核心心跳而落闸。只写 ``component-validation:`` 命名空间。"""
    if not isinstance(transport, ValidationTransport):
        raise ValueError('validation heartbeat requires an isolated ValidationTransport')
    for _ in range(seconds):
        transport.set_key(HEARTBEAT_CORE, Heartbeat(source='core', at=clock()).model_dump_json())
        sleep(1.0)


def collect(transport, client_order_ids: list[str]) -> dict:
    out = {cid: {'dispatch': [], 'rejects': []} for cid in client_order_ids}
    for msg in transport.inner.all_messages(transport.namespace + ':' + DISPATCH):
        event = DispatchStatusEvent.from_payload(msg.payload)
        if event.client_order_id in out:
            out[event.client_order_id]['dispatch'].append(event.state)
    for msg in transport.inner.all_messages(transport.namespace + ':' + REJECTS):
        event = RejectEvent.from_payload(msg.payload)
        if event.client_order_id in out:
            out[event.client_order_id]['rejects'].append(event.reason)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config', default=None)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--strategy-id', required=True)
    parser.add_argument('--scenario', action='append', choices=sorted(SCENARIOS), default=[])
    parser.add_argument('--wait', type=float, default=20.0)
    parser.add_argument('--kill', choices=('on', 'off'), default=None,
                        help='只在验证命名空间设置或清除远程 KILL 键')
    parser.add_argument('--core-heartbeat-seconds', type=int, default=0,
                        help='只发布验证命名空间的模拟核心心跳这么多秒（不发指令）')
    args = parser.parse_args(argv)
    from qsys.live.config import load_deploy_config
    from qsys.live.redis_transport import RedisTransport
    transport = ValidationTransport(RedisTransport.from_config(load_deploy_config(args.config)),
                                    args.namespace)
    if args.kill:
        set_kill(transport, args.kill == 'on')
        print(json.dumps({'kill': args.kill}))
        return 0
    if args.core_heartbeat_seconds:
        run_core_heartbeat(transport, seconds=args.core_heartbeat_seconds)
        return 0
    if not args.scenario:
        parser.error('--scenario required unless --core-heartbeat-seconds is given')
    sent = submit(transport, scenarios=args.scenario, strategy_id=args.strategy_id,
                  now=datetime.now())
    time.sleep(args.wait)
    result = collect(transport, [s['client_order_id'] for s in sent])
    print(json.dumps({'sent': sent, 'result': result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
