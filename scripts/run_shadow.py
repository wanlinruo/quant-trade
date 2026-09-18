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
from qsys.ops.alerts import AlertBus, LogAlertSink, WebhookAlertSink
from qsys.paper.calendar import load_trading_calendar
from qsys.paper.quotes import QuoteClient

log = structlog.get_logger("shadow-core")


def main() -> int:
    parser = argparse.ArgumentParser(description="qsys Linux 影子策略核心")
    parser.add_argument("--strategy", default="strategies/structural_v1_2.py")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--state", default="var/shadow-state.sqlite3")
    parser.add_argument("--config", default=None)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--lock", default="var/shadow-core.lock")
    args = parser.parse_args()

    config = load_deploy_config(args.config)
    if config.safety.mode != "shadow" or config.safety.orders_enabled:
        raise RuntimeError(
            "影子核心只在 [safety] mode='shadow' 且 orders_enabled=false 时启动"
        )
    transport = RedisTransport.from_config(config)
    sinks = [LogAlertSink()]
    if config.alerts.webhook_url:
        sinks.append(WebhookAlertSink(
            config.alerts.webhook_url, timeout=config.alerts.timeout_seconds
        ))
    alerts = AlertBus(sinks)
    runtime = LiveRuntime(
        Path(args.strategy), Path(args.warehouse), load_trading_calendar(args.config),
        QuoteClient(transport, config.streams, consumer="shadow-core"),
        transport, config.streams, LiveStore(args.state),
        local_kill_path=Path("var/SHADOW_KILL"),
        alerts=alerts, risk=config.risk, shadow=True,
    )
    guard = TickFailureGuard(alerts)
    with single_instance(args.lock):
        log.info("shadow_core.started", strategy=args.strategy)
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
