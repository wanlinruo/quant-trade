"""Windows 执行代理启动入口。

在云 Windows 上运行：
  uv run python scripts/run_agent.py --account <资金账号> --qmt <miniQMT路径>
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import structlog

from qsys.agent.execution_agent import ExecutionAgent
from qsys.agent.locking import single_instance
from qsys.agent.report_ledger import SQLiteReportLedger
from qsys.backtest.fees import AShareFeeModel
from qsys.live.config import load_deploy_config
from qsys.live.redis_transport import RedisTransport
from qsys.live.safety import SafetyPolicy, TradingMode

log = structlog.get_logger("agent")

POLL_SECONDS = 1.0
ACCOUNT_PUBLISH_EVERY = 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows 执行代理")
    parser.add_argument("--account", required=True)
    parser.add_argument("--qmt", required=True, help="miniQMT userdata 路径")
    parser.add_argument("--config", default=None)
    parser.add_argument("--ledger", default="var/agent-ledger.sqlite3")
    parser.add_argument("--lock", default="var/agent.lock")
    args = parser.parse_args()

    from qsys.agent.xtquant_adapter import XtQuantAdapter

    cfg = load_deploy_config(args.config)
    transport = RedisTransport.from_config(cfg)
    adapter = XtQuantAdapter(args.account, args.qmt)
    agent = ExecutionAgent(
        transport, adapter, cfg.streams, consumer=cfg.consumer_name,
        ledger=SQLiteReportLedger(Path(args.ledger)),
        fee_model=AShareFeeModel(
            commission_rate=cfg.fees.commission_rate,
            min_commission=cfg.fees.min_commission,
            stamp_duty_rate=cfg.fees.stamp_duty_rate,
            transfer_fee_rate=cfg.fees.transfer_fee_rate,
        ),
        safety=SafetyPolicy(
            TradingMode(cfg.safety.mode), account_id=args.account,
            protected_account_ids=cfg.safety.protected_account_ids,
            orders_enabled=cfg.safety.orders_enabled,
            local_kill_path=cfg.safety.local_kill_path,
        ),
    )

    with single_instance(args.lock):
        log.info("agent.started", account=args.account, mode=cfg.safety.mode)
        ticks = 0
        consecutive_errors = 0
        while True:
            try:
                agent.enforce_core_heartbeat()
                agent.run_once()
                agent.beat()
                if ticks % ACCOUNT_PUBLISH_EVERY == 0:
                    agent.publish_account()
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                log.error("agent.loop_error", error=str(exc),
                          consecutive_errors=consecutive_errors)
                # 连续故障时在 Windows 本地独立落闸；即使 Redis 同时失联也生效。
                if consecutive_errors >= 3:
                    agent.engage_local_kill("three consecutive agent loop failures")
                    try:
                        adapter.cancel_all()
                    except Exception as cancel_exc:
                        log.error("agent.cancel_after_failure_failed", error=str(cancel_exc))
            ticks += 1
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
