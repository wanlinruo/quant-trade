"""每日服务质检：检查各定时任务当天该写的表到齐没有、数量对不对。

由 qsys-service-checks.timer 在交易日 20:00 触发（Tushare 19:00 补跑之后）。
结果写成服务 `service-checks` 的运行记录，服务管理页把每条检查挂回所属任务。

先跑一次指标字典验证（当日增量 + 当年至今全量，写 `metric_checks`），再做各任务
的到货检查——这样「增量质检阻断项」读到的是当晚最新规则的结果，同步失败那天也有
结果。除 `metric_checks` 与样本外**不修任何数据**。

用法：
  uv run python scripts/service_checks.py --warehouse warehouse
  uv run python scripts/service_checks.py --day 2026-09-16      # 复查某一天
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from qsys.core.calendar import last_closed_session
from qsys.data.catalog.verification import verify_session
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.ops.service_checks import (
    BAD, CHECKS, OK, REPAIRED, SKIP, WARN, CheckContext, earlier_failures, mark_repaired, step_name,
)
from qsys.ops.services import RUNNING, SKIPPED, ServiceRun, effective_status, load_runs
from qsys.paper.calendar import load_trading_calendar


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"))
    parser.add_argument("--day", type=date.fromisoformat, default=None,
                        help="要检查的交易日；不给则取最近一个已收盘交易日")
    args = parser.parse_args()

    with ServiceRun.open(args.service_runs, "service-checks") as run:
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
        sessions = calendar.sessions_in_range(day - timedelta(days=40), day)
        warehouse = Warehouse(args.warehouse)
        ctx = CheckContext(
            warehouse, WarehouseQuery(warehouse), day,
            sessions[-2] if len(sessions) > 1 else None, sessions,
            # 任务在交易日当天执行；复查旧日子时读那一天的运行记录
            load_runs(args.service_runs, day, day + timedelta(days=1)),
        )
        with run.step("指标字典验证", interface="仓库内规则",
                      table="metric_checks") as step:
            # 同步与验证各自峰值约 5 GB，节点只有 7.5 GB：同步还没跑完就跳过，
            # 让它先写完自己的增量结果，不去抢内存。
            running = [r for r in ctx.runs
                       if r.get("service") == "tushare-sync"
                       and effective_status(r, now=datetime.now()) == RUNNING]
            if running:
                step.skip(f"{running[-1]['run_id']} 同步仍在运行，本次不重复验证")
            else:
                summary = verify_session(warehouse, day)
                step.rows = summary.rows
                step.detail = (f"当日增量 + {day.year} 年至今全量：{summary.rows} 条结果，"
                               f"阻断 {summary.blocking} 条，警告 {summary.warnings} 条")
                if summary.failed_checks:
                    # 阻断项由下面的「增量质检阻断项」统一判定，这里只记录跑了什么
                    step.detail += "。未通过：" + "、".join(summary.failed_checks)

        failed = 0
        # 同一交易日早先的质检（本次记录已落盘，但还没有任何步骤）
        failures = earlier_failures(ctx.runs, day)
        for check in CHECKS:
            with run.step(step_name(check), interface="只读仓库与运行记录",
                          table=check.table) as step:
                result = mark_repaired(check.fn(ctx), failures.get(step_name(check)))
                step.rows = result.rows
                text = f"期望：{result.expected}；实际：{result.actual}"
                if result.detail:
                    text += f"。{result.detail}"
                if result.status == BAD:
                    failed += 1
                    step.fail(text)
                elif result.status == WARN:
                    step.warn(text)
                elif result.status == SKIP:
                    step.skip(text)
                elif result.status == REPAIRED:
                    step.status, step.detail = REPAIRED, text
                else:
                    assert result.status == OK
                    step.detail = text
        print(f"{day} 服务质检：{len(CHECKS)} 项，未通过 {failed} 项")
        if run.status == SKIPPED:
            return 0
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
