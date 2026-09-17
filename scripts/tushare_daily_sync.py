"""每日增量同步的命令行入口。逻辑在 qsys.data.pipeline.tushare_sync。

用法:
    uv run python scripts/tushare_daily_sync.py --warehouse /data/qsys/warehouse
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import structlog

from qsys.data.pipeline.tushare_sync import (
    completed_calendar_end,
    last_open_day,
    refresh_derived,
    sync,
)
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.storage import Warehouse
from qsys.live.config import load_tushare_config
from qsys.ops.services import ServiceRun, record_step

log = structlog.get_logger(__name__)


def run_incremental_verification(warehouse: Warehouse, day: date) -> int:
    """执行当日增量规则、落盘结果并返回 blocking 指标结果数。"""
    from qsys.data.catalog import checks as catalog_checks
    from qsys.data.query import WarehouseQuery

    now = datetime.now()
    context = catalog_checks.CheckContext(
        WarehouseQuery(warehouse), warehouse, day, day
    )
    frame = catalog_checks.run_checks(
        context,
        now.strftime("%Y%m%dT%H%M%S%f"),
        now,
        scope=catalog_checks.Scope.INCREMENTAL,
    )
    catalog_checks.write_results(warehouse, frame)
    return frame.filter(
        (~pl.col("passed")) & (pl.col("severity") == "blocking")
    ).height


def after_sync(warehouse: Warehouse, day: date, *, ok: bool,
               run: ServiceRun | None = None) -> None:
    """只在同步成功后验证；验证器故障不得改变同步任务退出码（运行记录里记为警告）。"""
    if not ok:
        log.info("verify.skipped_after_failed_sync", day=str(day))
        return
    with record_step(run, "增量质检", interface="仓库内规则", table="metric_checks") as step:
        try:
            blocking = run_incremental_verification(warehouse, day)
        except Exception as exc:
            # 数据已经落盘。把同步改报失败会误导 systemd/Persistent 重补已完成数据。
            log.warning("verify.hook_crashed", day=str(day), error=str(exc))
            step.warn(f"质检程序出错：{exc}")
            return
        log.info("verify.after_sync_done", day=str(day), blocking=blocking)
        if blocking:
            step.warn(f"阻断级规则未通过 {blocking} 项，详见数据管理 → 指标字典")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--day", type=date.fromisoformat, default=None,
                        help="指定交易日；不给则取最近一个已收盘交易日")
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"),
                        help="运行记录目录（服务管理页读取）")
    args = parser.parse_args()

    with ServiceRun.open(args.service_runs, "tushare-sync") as run:
        client = TushareClient(load_tushare_config(args.config).token)
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        # 16:00 前当日数据可能尚未结算；默认只允许选到前一自然日。
        calendar_end = completed_calendar_end(now)
        with run.step("确定交易日", interface="Tushare trade_cal") as step:
            day = args.day or last_open_day(client, calendar_end)
            step.detail = str(day) if day else "最近 30 天没有交易日"
        if day is None:
            run.skip("最近 30 天没有交易日")
            print("最近 30 天没有交易日，退出")
            return 0
        run.session = day

        warehouse = Warehouse(args.warehouse)
        counts = sync(TushareProvider(client), warehouse, day, run=run)
        counts.update(refresh_derived(warehouse, day, run=run))
        after_sync(warehouse, day, ok=True, run=run)
    print(f"=== {day} ===")
    for name, rows in counts.items():
        print(f"  {name:22s} {rows:>9,} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
