"""Linux-side durable paper-trading scheduler.

The process consumes only QMT quote responses.  It never constructs a
LiveBroker or writes to the real order/control streams.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import structlog

from qsys.live.config import load_deploy_config
from qsys.live.messages import Heartbeat
from qsys.live.redis_transport import RedisTransport
from qsys.paper.calendar import load_trading_calendar
from qsys.paper.quotes import QuoteClient, QuoteUnavailable
from qsys.paper.runtime import PaperRuntime
from qsys.paper.store import PaperStore

log = structlog.get_logger("paper-runner")


def main() -> int:
    parser = argparse.ArgumentParser(description="实时模拟交易运行器")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--workspace", default="var/workbench")
    parser.add_argument("--config", default=None)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--account", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.account:
        return run_worker(args)
    return run_supervisor(args)


# 行情连续不可用超过这么久才判定账户失败。偶发一次超时（网关忙、网络抖动）
# 不该让账户永久停下；持续不可用仍要失败关闭，不能无限重试掩盖故障。
QUOTE_OUTAGE_LIMIT = timedelta(minutes=3)


def run_account_loop(
    store: PaperStore, ident: str, build_runtime: Callable[[], object], *,
    poll: float, outage_limit: timedelta = QUOTE_OUTAGE_LIMIT,
    now: Callable[[], datetime] = datetime.now,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """驱动单个模拟账户，直到它不再处于 scheduled/running。返回进程退出码。

    行情暂不可用（`QuoteUnavailable`）时记 warning、**从库里重建 runtime**
    再重试：事件跑到一半失败，策略的内存状态可能已被改了一部分，不能带着
    它继续。连续不可用超过 `outage_limit`，或出现任何其他异常，账户标为 error。
    """
    outage_since: datetime | None = None
    try:
        runtime = build_runtime()
        while True:
            account = store.account(ident)
            if account is None or account.status not in ("scheduled", "running"):
                return 0
            try:
                runtime.tick()
            except QuoteUnavailable as exc:
                moment = now()
                if outage_since is None:
                    outage_since = moment
                    store.log(ident, "warning", f"行情暂不可用，稍后重试：{exc}")
                    log.warning("paper.quote_unavailable", account_id=ident, error=str(exc))
                if moment - outage_since >= outage_limit:
                    raise
                runtime = build_runtime()
            else:
                if outage_since is not None:
                    store.log(ident, "info",
                              f"行情已恢复，中断 {(now() - outage_since).total_seconds():.0f} 秒")
                    outage_since = None
            sleep(max(0.2, poll))
    except Exception as exc:
        log.error("paper.account_failed", account_id=ident, error=str(exc))
        store.mark_error(ident, str(exc))
        return 1


def run_worker(args) -> int:
    deploy = load_deploy_config(args.config)
    workspace = Path(args.workspace)
    store = PaperStore(workspace / "workbench.sqlite3")
    try:
        calendar = load_trading_calendar(args.config)
        client = QuoteClient(RedisTransport.from_config(deploy), deploy.streams,
                             consumer=f"paper-{args.account}")
    except Exception as exc:
        # 与运行期异常同一处理：失败关闭，避免 supervisor 每个轮询周期重启一次
        log.error("paper.account_failed", account_id=args.account, error=str(exc))
        store.mark_error(args.account, str(exc))
        return 1
    return run_account_loop(
        store, args.account,
        lambda: PaperRuntime(args.account, store, Path(args.warehouse), calendar,
                             client, workspace),
        poll=args.poll,
    )


def run_supervisor(args) -> int:

    deploy = load_deploy_config(args.config)
    transport = RedisTransport.from_config(deploy)
    workspace = Path(args.workspace)
    store = PaperStore(workspace / "workbench.sqlite3")
    workers: dict[str, subprocess.Popen] = {}
    heartbeat_failed = False
    log.info("paper_runner.started")
    while True:
        active = {a.id: a for a in store.accounts()
                  if a.status in ("scheduled", "running")}
        for ident in list(workers):
            if ident not in active:
                process = workers.pop(ident)
                if process.poll() is None:
                    process.terminate()
        for ident in active:
            process = workers.get(ident)
            if process is not None and process.poll() is None:
                continue
            command = [
                sys.executable, str(Path(__file__).resolve()),
                "--warehouse", args.warehouse, "--workspace", args.workspace,
                "--poll", str(args.poll), "--account", ident,
            ]
            if args.config:
                command += ["--config", args.config]
            workers[ident] = subprocess.Popen(command)
            log.info("paper.worker_started", account_id=ident,
                     pid=workers[ident].pid)
        try:
            transport.set_key(
                f"{deploy.streams.heartbeat}:paper",
                Heartbeat(source="paper", at=datetime.now()).model_dump_json(),
            )
            if heartbeat_failed:
                log.info("paper.redis_recovered")
            heartbeat_failed = False
        except Exception as exc:
            # An empty supervisor remains useful while Redis/QMT is being
            # provisioned.  Account workers still fail closed and mark the
            # affected account as error when quotes are unavailable.
            if not heartbeat_failed:
                log.warning("paper.redis_unavailable", error=str(exc))
            heartbeat_failed = True
        time.sleep(max(0.2, args.poll))


if __name__ == "__main__":
    sys.exit(main())
