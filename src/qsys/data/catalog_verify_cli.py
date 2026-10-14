"""Run the production catalog verifier from an installed qsys release."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from qsys.core.calendar import last_closed_session
from qsys.data.catalog.verification import verify_session
from qsys.data.storage import Warehouse
from qsys.ops.services import RUNNING, ServiceRun, effective_status, load_runs
from qsys.paper.calendar import load_trading_calendar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"))
    parser.add_argument("--day", type=date.fromisoformat, default=None,
                        help="要验证的交易日；不给则取最近一个已收盘交易日")
    args = parser.parse_args(argv)

    with ServiceRun.open(args.service_runs, "catalog-verify") as run:
        with run.step("确定交易日", interface="Tushare trade_cal") as step:
            calendar = load_trading_calendar(args.config)
            if args.day is not None and not calendar.is_trading_day(args.day):
                run.skip(f"{args.day} 不是交易日")
                step.detail = run.note
                return 0
            day = args.day or last_closed_session(calendar, datetime.now())
            step.detail = str(day)
        if day is None:
            run.skip("没有已收盘的交易日")
            return 0
        run.session = day

        with run.step("指标字典验证", interface="仓库内规则", table="metric_checks") as step:
            runs = load_runs(args.service_runs, day, day + timedelta(days=1))
            running = [
                item for item in runs
                if item.get("service") == "tushare-sync"
                and effective_status(item, now=datetime.now()) == RUNNING
            ]
            if running:
                step.skip(f"{running[-1]['run_id']} 同步仍在运行，本次不重复验证")
                print(f"{day} 指标字典验证：同步仍在运行，跳过")
                return 0
            summary = verify_session(Warehouse(args.warehouse), day)
            step.rows = summary.rows
            step.detail = (
                f"当日增量 + {day.year} 年至今全量：{summary.rows} 条结果，"
                f"阻断 {summary.blocking} 条，警告 {summary.warnings} 条"
            )
            if summary.failed_checks:
                step.detail += "。未通过：" + "、".join(summary.failed_checks)
        print(
            f"{day} 指标字典验证：{summary.rows} 条结果，"
            f"阻断 {summary.blocking} 条，警告 {summary.warnings} 条"
        )
        return 0

