"""每日服务质检：检查各定时任务当天该写的表到齐没有、数量对不对。

由 qsys-service-checks.timer 在交易日 20:00 触发（Tushare 19:00 补跑之后）。
结果写成服务 `service-checks` 的运行记录，服务管理页把每条检查挂回所属任务。

指标字典的验证是另一个任务（`scripts/catalog_verify.py`，19:40），本脚本只读它
写下的结果并核对；**只读仓库**，不修任何数据。

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
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.ops.service_checks import (
    BAD, CHECKS, OK, REPAIRED, SKIP, WARN, CheckContext, earlier_failures, mark_repaired, step_name,
)
from qsys.ops.services import SKIPPED, ServiceRun, load_runs
from qsys.paper.calendar import load_trading_calendar


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"))
    parser.add_argument("--shadow-state", type=Path, default=Path("var/shadow-state.sqlite3"),
                        help="影子盘状态库；用于核对当日策略事件是否都跑过")
    parser.add_argument("--workbench", type=Path,
                        default=Path("var/workbench/workbench.sqlite3"),
                        help="模拟盘工作台；同上")
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
            shadow_state=args.shadow_state, workbench=args.workbench,
        )
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
