"""Linux 影子策略核心：完整运行策略与风控，但永不写真实订单流。"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import structlog

from qsys.agent.locking import single_instance
from qsys.live.config import load_deploy_config
from qsys.live.redis_transport import RedisTransport
from qsys.live.runtime import LiveRuntime
from qsys.live.store import LiveStore
from qsys.live.tick_guard import TickFailureGuard
from qsys.ops.alerts import AlertBus, ledger, strategy_sinks
from qsys.paper.calendar import load_trading_calendar
from qsys.live.lineage import StrategySource, resolve_strategy
from qsys.paper.quotes import DECISION_TIMEOUT, QuoteClient
from qsys.data.cutover import add_facade_argument, apply_facade_argument

log = structlog.get_logger("shadow-core")


def main() -> int:
    parser = argparse.ArgumentParser(description="qsys Linux 影子策略核心")
    parser.add_argument("--strategy", default=None,
                        help="不给则按配置 [strategy]（可承袭模拟账户）确定")
    parser.add_argument("--workspace", default="var/shadow",
                        help="承袭模拟账户时，策略源码快照写在这里")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--state", default="var/shadow-state.sqlite3")
    parser.add_argument("--journal-warehouse", default=None,
                        help="影子盘独占运行台账；默认兼容写入主 warehouse")
    parser.add_argument("--alert-ledger", default="var/alerts")
    parser.add_argument("--config", default=None)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--lock", default="var/shadow-core.lock")
    parser.add_argument("--kill-switch", default="var/SHADOW_KILL")
    parser.add_argument(
        "--validation-namespace", default=None,
        help="仅隔离预生产使用，统一隔离所有Redis key/stream（含kill与intents）",
    )
    add_facade_argument(parser)
    args = parser.parse_args()
    apply_facade_argument(args.market_data_facade)

    config = load_deploy_config(args.config)
    if config.safety.mode != "shadow" or config.safety.orders_enabled:
        raise RuntimeError(
            "影子核心只在 [safety] mode='shadow' 且 orders_enabled=false 时启动"
        )
    with single_instance(args.lock):
        transport = RedisTransport.from_config(config)
        if args.validation_namespace is not None:
            from qsys.live.validation_transport import ValidationTransport

            transport = ValidationTransport(transport, args.validation_namespace)
        # 判断解析后的地址：只配了影子那个、没配 webhook_url 时也要能发。
        # CRITICAL 同时进运维群——停机既是交易事件也是运维事故。
        alerts = AlertBus(strategy_sinks(
            config.alerts.strategy_url(shadow=True), config.alerts.ops_url,
            timeout=config.alerts.timeout_seconds,
        ), ledger(args.alert_ledger))
        if args.strategy:
            source = StrategySource(Path(args.strategy))
        else:
            source = resolve_strategy(config.strategy, Path(args.workspace))
            if source.follows_paper:
                log.info("shadow_core.follows_paper", account=source.paper_account_id,
                         name=source.paper_account_name, status=source.paper_status,
                         backtest=source.backtest_id, version=source.version_id,
                         params=dict(source.params))
        runtime = LiveRuntime(
            source.path, Path(args.warehouse), load_trading_calendar(args.config),
            QuoteClient(transport, config.streams, consumer="shadow-core",
                        timeout=DECISION_TIMEOUT),
            transport, config.streams, LiveStore(args.state),
            local_kill_path=Path(args.kill_switch),
            journal_warehouse=(
                Path(args.journal_warehouse) if args.journal_warehouse else None
            ),
            alerts=alerts, risk=config.risk, shadow=True, params=dict(source.params),
        )
        guard = TickFailureGuard(alerts)
        with runtime.heartbeats:
            log.info("shadow_core.started", strategy=str(source.path),
                     follows=source.paper_account_id or None)
            while True:
                try:
                    runtime.tick()
                except Exception as exc:
                    # 每秒重复同一条错误会把日志淹掉（2026-09-18 刷了 2 万条）：
                    # 降频打印，持续失败由 guard 报一次告警。
                    if guard.failed(str(exc), now=datetime.now()):
                        log.error("shadow_core.tick_failed", error=str(exc))
                else:
                    guard.succeeded(now=datetime.now())
                time.sleep(max(0.2, args.poll))


if __name__ == "__main__":
    sys.exit(main())
