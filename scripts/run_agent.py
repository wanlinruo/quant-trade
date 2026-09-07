"""Windows 执行代理启动入口。

在云 Windows 上运行：
  uv run python scripts/run_agent.py --account <资金账号> --qmt <miniQMT路径>
"""

from __future__ import annotations

import argparse
import sys
import time

import structlog

from qsys.agent.execution_agent import ExecutionAgent
from qsys.live.config import load_deploy_config
from qsys.live.redis_transport import RedisTransport

log = structlog.get_logger("agent")

POLL_SECONDS = 1.0
ACCOUNT_PUBLISH_EVERY = 30


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows 执行代理")
    parser.add_argument("--account", required=True)
    parser.add_argument("--qmt", required=True, help="miniQMT userdata 路径")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    from qsys.agent.xtquant_adapter import XtQuantAdapter

    cfg = load_deploy_config(args.config)
    transport = RedisTransport.from_config(cfg)
    adapter = XtQuantAdapter(args.account, args.qmt)
    agent = ExecutionAgent(transport, adapter, cfg.streams, consumer=cfg.consumer_name)

    log.info("agent.started", account=args.account)
    ticks = 0
    while True:
        try:
            agent.run_once()
            agent.beat()
            if ticks % ACCOUNT_PUBLISH_EVERY == 0:
                agent.publish_account()
        except Exception as exc:  # 单次循环失败不应终止代理
            log.error("agent.loop_error", error=str(exc))
        ticks += 1
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
