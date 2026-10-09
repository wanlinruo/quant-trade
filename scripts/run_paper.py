"""Linux-side durable paper-trading scheduler.

The process consumes only QMT quote responses.  It never constructs a
LiveBroker or writes to the real order/control streams.
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, time as wall_time, timedelta
from pathlib import Path

import structlog

from qsys.live.config import load_deploy_config
from qsys.live.messages import Heartbeat
from qsys.live.redis_transport import RedisTransport
from qsys.ops.health import CALENDAR_UNKNOWN, HeartbeatCadence, heartbeat_policy, trading_phase
from qsys.paper.calendar import load_trading_calendar
from qsys.paper.quotes import DECISION_TIMEOUT, QuoteClient, QuoteUnavailable
from qsys.paper.runtime import PaperRuntime
from qsys.paper.store import PaperStore
from qsys.data.cutover import add_facade_argument, apply_facade_argument

log = structlog.get_logger("paper-runner")


def main() -> int:
    parser = argparse.ArgumentParser(description="实时模拟交易运行器")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--workspace", default="var/workbench")
    parser.add_argument("--config", default=None)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--account", default="", help=argparse.SUPPRESS)
    add_facade_argument(parser)
    args = parser.parse_args()
    apply_facade_argument(args.market_data_facade)

    if args.account:
        return run_worker(args)
    return run_supervisor(args)


# 行情连续不可用超过这么久才判定账户失败。偶发一次超时（网关忙、网络抖动）
# 不该让账户永久停下；持续不可用仍要失败关闭，不能无限重试掩盖故障。
QUOTE_OUTAGE_LIMIT = timedelta(minutes=3)
STORAGE_OUTAGE_LIMIT = timedelta(minutes=3)
IDLE_POLL_SECONDS = 60.0


def _transient_storage_error(exc: BaseException) -> bool:
    """SQLite 的短暂文件/锁故障不应永久停掉模拟账户。"""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return "unable to open database file" in message or "database is locked" in message


def _poll_delay(moment: datetime, poll: float) -> float:
    """盘中及时驱动事件；盘前、午休和收盘后降低空转频率。"""
    active = (wall_time(9, 15) <= moment.time() <= wall_time(11, 30)
              or wall_time(13, 0) <= moment.time() <= wall_time(15, 30))
    if active:
        return max(0.2, poll)
    idle_delay = max(IDLE_POLL_SECONDS, poll)
    for hour, minute in ((9, 15), (13, 0)):
        boundary = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if boundary > moment:
            # Never let a low-frequency idle poll sleep across a strategy
            # event boundary (09:15 session start or 13:00 afternoon start).
            return max(0.2, min(idle_delay, (boundary - moment).total_seconds()))
    return idle_delay


def _best_effort_account_log(store: PaperStore, ident: str, level: str,
                             message: str) -> None:
    """诊断日志写失败不能盖过原故障，更不能因此永久停号。"""
    try:
        store.log(ident, level, message)
    except sqlite3.OperationalError as exc:
        if not _transient_storage_error(exc):
            raise
        log.warning("paper.account_log_deferred", account_id=ident,
                    level=level, message=message, error=str(exc))


def run_account_loop(
    store: PaperStore, ident: str, build_runtime: Callable[[], object], *,
    poll: float, outage_limit: timedelta = QUOTE_OUTAGE_LIMIT,
    storage_outage_limit: timedelta = STORAGE_OUTAGE_LIMIT,
    now: Callable[[], datetime] = datetime.now,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """驱动单个模拟账户，直到它不再处于 scheduled/running。返回进程退出码。

    行情或 SQLite 暂不可用时，**从库里重建 runtime** 再有限重试：事件跑到
    一半失败，策略的内存状态可能已被改了一部分，不能带着它继续。连续不可用
    超过各自时限，或出现其他异常，账户才标为 error。
    """
    outage_since: datetime | None = None
    storage_outage_since: datetime | None = None
    runtime = None
    try:
        while True:
            try:
                if runtime is None:
                    runtime = build_runtime()
                account = store.account(ident)
                if account is None or account.status not in ("scheduled", "running"):
                    return 0
                runtime.tick()
            except QuoteUnavailable as exc:
                moment = now()
                if outage_since is None:
                    outage_since = moment
                    _best_effort_account_log(
                        store, ident, "warning", f"行情暂不可用，稍后重试：{exc}"
                    )
                    log.warning("paper.quote_unavailable", account_id=ident, error=str(exc))
                if moment - outage_since >= outage_limit:
                    raise
                runtime = None
            except sqlite3.OperationalError as exc:
                if not _transient_storage_error(exc):
                    raise
                moment = now()
                if storage_outage_since is None:
                    storage_outage_since = moment
                    log.warning("paper.storage_unavailable", account_id=ident,
                                error=str(exc))
                if moment - storage_outage_since >= storage_outage_limit:
                    raise
                # 连接恢复后必须从持久化状态重建，不能沿用可能只执行了一半的内存状态。
                runtime = None
            else:
                if outage_since is not None:
                    _best_effort_account_log(
                        store, ident, "info",
                        f"行情已恢复，中断 {(now() - outage_since).total_seconds():.0f} 秒",
                    )
                    outage_since = None
                if storage_outage_since is not None:
                    _best_effort_account_log(
                        store, ident, "info",
                        f"账户存储已恢复，中断 {(now() - storage_outage_since).total_seconds():.0f} 秒",
                    )
                    log.info("paper.storage_recovered", account_id=ident)
                    storage_outage_since = None
            sleep(_poll_delay(now(), poll))
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
                             consumer=f"paper-{args.account}", timeout=DECISION_TIMEOUT)
    except Exception as exc:
        # 与运行期异常同一处理：失败关闭，避免 supervisor 每个轮询周期重启一次
        log.error("paper.account_failed", account_id=args.account, error=str(exc))
        store.mark_error(args.account, str(exc))
        return 1
    return run_account_loop(
        store, args.account,
        lambda: PaperRuntime(args.account, store, Path(args.warehouse), calendar,
                             client, workspace, deploy.alerts.ops_url),
        poll=args.poll,
    )


def run_supervisor(args) -> int:

    deploy = load_deploy_config(args.config)
    transport = RedisTransport.from_config(deploy)
    workspace = Path(args.workspace)
    store = PaperStore(workspace / "workbench.sqlite3")
    workers: dict[str, subprocess.Popen] = {}
    heartbeat_failed = False
    heartbeat = HeartbeatCadence()
    calendar = None
    calendar_day = None
    next_calendar_retry = 0.0
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
        monotonic = time.monotonic()
        wall = datetime.now()
        if calendar_day != wall.date() and monotonic >= next_calendar_retry:
            try:
                calendar = load_trading_calendar(args.config)
                calendar_day = wall.date()
            except Exception as exc:
                calendar = None
                next_calendar_retry = monotonic + 300
                log.warning("paper.calendar_unavailable", error=str(exc))
        sessions = None if calendar is None else set(
            calendar.sessions_in_range(wall.date(), wall.date())
        )
        phase = trading_phase(wall, sessions)
        policy = heartbeat_policy("paper", phase, active=bool(active))
        if heartbeat.due(monotonic, policy.expected_interval_s):
            try:
                transport.set_key(
                    f"{deploy.streams.heartbeat}:paper",
                    Heartbeat(
                        source="paper", at=datetime.now(),
                        expected_interval_s=policy.expected_interval_s, phase=phase,
                    ).model_dump_json(),
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
