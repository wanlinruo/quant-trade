"""Windows 执行代理启动入口。

在云 Windows 上运行：
  uv run python scripts/run_agent.py --account <资金账号> --qmt <miniQMT路径>
"""

from __future__ import annotations

import argparse
import ast
import re
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


def _load_qmt_runtime(path: str) -> tuple[str, str]:
    """Read the two flat QMT fields without adding a YAML dependency.

    Reading the UTF-8 file in Python is important on Windows PowerShell 5,
    whose default text encoding can corrupt non-ASCII userdata paths.
    """
    values: dict[str, str] = {}
    wanted = {"qmt_account_id", "qmt_userdata_path"}
    for raw_line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$", raw_line)
        if not match or match.group(1) not in wanted:
            continue
        value = match.group(2)
        if value[:1] in {"'", '"'}:
            value = str(ast.literal_eval(value))
        values[match.group(1)] = value
    missing = sorted(wanted - values.keys())
    if missing:
        raise ValueError(f"runtime YAML 缺少字段: {', '.join(missing)}")
    return values["qmt_account_id"], values["qmt_userdata_path"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows 执行代理")
    parser.add_argument("--account")
    parser.add_argument("--qmt", help="miniQMT userdata 路径")
    parser.add_argument(
        "--runtime-yaml",
        help="从已有私有 YAML 的 qmt_account_id/qmt_userdata_path 读取连接参数",
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--ledger", default="var/agent-ledger.sqlite3")
    parser.add_argument("--lock", default="var/agent.lock")
    args = parser.parse_args()

    if args.runtime_yaml:
        if args.account or args.qmt:
            parser.error("--runtime-yaml 不能与 --account/--qmt 同时使用")
        args.account, args.qmt = _load_qmt_runtime(args.runtime_yaml)
    elif not args.account or not args.qmt:
        parser.error("必须提供 --runtime-yaml，或同时提供 --account 和 --qmt")

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
