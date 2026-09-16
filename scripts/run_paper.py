"""Linux-side durable paper-trading scheduler.

The process consumes only QMT quote responses.  It never constructs a
LiveBroker or writes to the real order/control streams.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import structlog

from qsys.core.calendar import AShareCalendar
from qsys.data.providers.tushare_client import TushareClient
from qsys.live.config import load_deploy_config, load_tushare_config
from qsys.live.messages import Heartbeat
from qsys.live.redis_transport import RedisTransport
from qsys.paper.quotes import QuoteClient
from qsys.paper.runtime import PaperRuntime
from qsys.paper.store import PaperStore

log = structlog.get_logger("paper-runner")


def trading_calendar(config_path: str | None) -> AShareCalendar:
    cfg = load_tushare_config(config_path)
    today = date.today()
    rows = TushareClient(cfg.token).call("trade_cal", {
        "exchange": "SSE",
        "start_date": (today - timedelta(days=370)).strftime("%Y%m%d"),
        "end_date": (today + timedelta(days=370)).strftime("%Y%m%d"),
        "is_open": "1",
    }, "cal_date,is_open")
    sessions = [datetime.strptime(str(row["cal_date"]), "%Y%m%d").date()
                for row in rows if str(row.get("is_open", "1")) == "1"]
    if not sessions or sessions[-1] < today + timedelta(days=30):
        raise RuntimeError("Tushare 交易日历为空或未来覆盖不足 30 天")
    return AShareCalendar(sessions)


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


def run_worker(args) -> int:
    deploy = load_deploy_config(args.config)
    workspace = Path(args.workspace)
    store = PaperStore(workspace / "workbench.sqlite3")
    try:
        runtime = PaperRuntime(
            args.account, store, Path(args.warehouse), trading_calendar(args.config),
            QuoteClient(RedisTransport.from_config(deploy), deploy.streams,
                        consumer=f"paper-{args.account}"), workspace,
        )
        while True:
            account = store.account(args.account)
            if account is None or account.status not in ("scheduled", "running"):
                return 0
            runtime.tick()
            time.sleep(max(0.2, args.poll))
    except Exception as exc:
        log.error("paper.account_failed", account_id=args.account, error=str(exc))
        store.mark_error(args.account, str(exc))
        return 1


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
