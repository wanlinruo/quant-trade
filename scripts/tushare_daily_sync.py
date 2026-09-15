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


def after_sync(warehouse: Warehouse, day: date, *, ok: bool) -> None:
    """只在同步成功后验证；验证器故障不得改变同步任务退出码。"""
    if not ok:
        log.info("verify.skipped_after_failed_sync", day=str(day))
        return
    try:
        blocking = run_incremental_verification(warehouse, day)
    except Exception as exc:
        # 数据已经落盘。把同步改报失败会误导 systemd/Persistent 重补已完成数据。
        log.warning("verify.hook_crashed", day=str(day), error=str(exc))
        return
    log.info("verify.after_sync_done", day=str(day), blocking=blocking)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--day", type=date.fromisoformat, default=None,
                        help="指定交易日；不给则取最近一个已收盘交易日")
    args = parser.parse_args()

    client = TushareClient(load_tushare_config(args.config).token)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    # 16:00 前当日数据可能尚未结算；默认只允许选到前一自然日。
    calendar_end = completed_calendar_end(now)
    day = args.day or last_open_day(client, calendar_end)
    if day is None:
        print("最近 30 天没有交易日，退出")
        return 0

    warehouse = Warehouse(args.warehouse)
    counts = sync(TushareProvider(client), warehouse, day)
    counts.update(refresh_derived(warehouse, day))
    after_sync(warehouse, day, ok=True)
    print(f"=== {day} ===")
    for name, rows in counts.items():
        print(f"  {name:22s} {rows:>9,} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
