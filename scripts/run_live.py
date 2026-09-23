"""Linux 实盘核心入口；默认配置为 disabled，不能仅靠启动脚本开启下单。"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import structlog

from qsys.live.config import load_deploy_config
from qsys.live.redis_transport import RedisTransport
from qsys.live.runtime import LiveRuntime
from qsys.live.store import LiveStore
from qsys.agent.locking import single_instance
from qsys.live.lineage import StrategySource, resolve_strategy
from qsys.paper.quotes import DECISION_TIMEOUT, QuoteClient
from qsys.ops.alerts import AlertBus, LogAlertSink, WebhookAlertSink
from qsys.paper.calendar import load_trading_calendar
from qsys.data.cutover import add_facade_argument, apply_facade_argument

log = structlog.get_logger("live-core")


def main() -> int:
    parser = argparse.ArgumentParser(description="qsys 实盘策略核心")
    parser.add_argument("--strategy", default=None,
                        help="不给则按配置 [strategy]（可承袭模拟账户）确定")
    parser.add_argument("--workspace", default="var/live",
                        help="承袭模拟账户时，策略源码快照写在这里")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--state", default="var/live-state.sqlite3")
    parser.add_argument("--config", default=None)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--lock", default="var/live-core.lock")
    add_facade_argument(parser)
    args = parser.parse_args()
    apply_facade_argument(args.market_data_facade)

    config = load_deploy_config(args.config)
    if config.safety.mode != "live" or not config.safety.orders_enabled:
        raise RuntimeError("实盘核心只在 [safety] mode='live' 且 orders_enabled=true 时启动")
    transport = RedisTransport.from_config(config)
    sinks = [LogAlertSink()]
    if config.alerts.webhook_url:
        sinks.append(WebhookAlertSink(
            config.alerts.webhook_url, timeout=config.alerts.timeout_seconds
        ))
    source = (StrategySource(Path(args.strategy)) if args.strategy
              else resolve_strategy(config.strategy, Path(args.workspace)))
    runtime = LiveRuntime(
        source.path, Path(args.warehouse), load_trading_calendar(args.config),
        QuoteClient(transport, config.streams, consumer="live-core",
                    timeout=DECISION_TIMEOUT),
        transport, config.streams, LiveStore(args.state),
        alerts=AlertBus(sinks),
        risk=config.risk, params=dict(source.params),
    )
    with single_instance(args.lock):
        log.info("live_core.started", strategy=str(source.path),
                 follows=source.paper_account_id or None)
        consecutive_errors = 0
        while True:
            try:
                runtime.tick()
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                log.error("live_core.tick_failed", error=str(exc),
                          consecutive_errors=consecutive_errors)
                if consecutive_errors == 3:
                    reason = "three consecutive live-core loop failures"
                    runtime.kill_switch.engage(reason)
                    runtime.broker.cancel_all()
                    runtime.alerts.critical("实盘核心连续故障熔断", f"{reason}: {exc}")
            time.sleep(max(0.2, args.poll))


if __name__ == "__main__":
    sys.exit(main())
