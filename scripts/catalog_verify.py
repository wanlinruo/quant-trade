"""指标字典验证：按字典里的规则验证当日数据，结果写 `metric_checks`。

由 qsys-catalog-verify.timer 在交易日 19:40 触发——Tushare 收盘同步（17:00 与
19:00 补跑）之后、每日服务质检（20:00）之前，质检因此读得到当晚的最新结果。

范围是「当日增量规则 + 当年至今的全量规则」，理由见
`qsys.data.catalog.verification`。整段历史仍走手工 `scripts/verify_metrics.py`。

用法：
  uv run python scripts/catalog_verify.py --warehouse warehouse
  uv run python scripts/catalog_verify.py --day 2026-09-16      # 复验某一天
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from qsys.core.calendar import last_closed_session
from qsys.data.catalog.verification import verify_session
from qsys.data.storage import Warehouse
from qsys.ops.services import RUNNING, ServiceRun, effective_status, load_runs
from qsys.paper.calendar import load_trading_calendar


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"))
    parser.add_argument("--day", type=date.fromisoformat, default=None,
                        help="要验证的交易日；不给则取最近一个已收盘交易日")
    args = parser.parse_args()

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
            # 同步与验证各自峰值约 5 GB，节点只有 7.5 GB：同步还没跑完就跳过，
            # 让它先写完自己的增量结果，不去抢内存。当晚服务质检会核对结果在不在。
            runs = load_runs(args.service_runs, day, day + timedelta(days=1))
            running = [r for r in runs
                       if r.get("service") == "tushare-sync"
                       and effective_status(r, now=datetime.now()) == RUNNING]
            if running:
                step.skip(f"{running[-1]['run_id']} 同步仍在运行，本次不重复验证")
                print(f"{day} 指标字典验证：同步仍在运行，跳过")
                return 0
            summary = verify_session(Warehouse(args.warehouse), day)
            step.rows = summary.rows
            step.detail = (f"当日增量 + {day.year} 年至今全量：{summary.rows} 条结果，"
                           f"阻断 {summary.blocking} 条，警告 {summary.warnings} 条")
            if summary.failed_checks:
                # 阻断项由当晚服务质检统一判定，这里只如实记录跑了什么
                step.detail += "。未通过：" + "、".join(summary.failed_checks)
        print(f"{day} 指标字典验证：{summary.rows} 条结果，阻断 {summary.blocking} 条，"
              f"警告 {summary.warnings} 条")
        return 0


if __name__ == "__main__":
    sys.exit(main())
