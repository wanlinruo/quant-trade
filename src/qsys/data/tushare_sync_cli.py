"""Tushare 每日增量同步的可安装命令行入口。

用法:
    qsys-tushare-sync --warehouse /data/qsys/warehouse
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import structlog

from qsys.data.pipeline.tushare_sync import (
    completed_calendar_end,
    last_open_day,
    next_open_day,
    refresh_derived,
    sync,
)
from qsys.data.pipeline.canonical_publish import publish_postclose
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.storage import Warehouse
from qsys.data.query import WarehouseQuery
from qsys.live.config import load_tushare_config
from qsys.ops.services import ServiceRun, record_step

log = structlog.get_logger(__name__)


def late_listing_minute_gaps(warehouse: Warehouse, day: date) -> list[str]:
    """找出上市日当天有成交、但 QMT 分钟线尚未落库的证券。

    只看 ``list_date == day``，避免把一般下载故障变成每次 Tushare 同步都会
    自动重试的无限恢复环；普通缺口仍由 service-checks 告警后人工处置。
    """
    query = WarehouseQuery(warehouse)
    listed = (
        query.instruments()
        .filter(pl.col("list_date") == day)["symbol"]
        .to_list()
    )
    if not listed:
        return []
    traded = query.tushare_daily(
        listed, day, day, columns=["symbol", "dt", "volume"]
    ).filter(pl.col("volume") > 0)
    if traded.height == 0:
        return []
    symbols = sorted(set(traded["symbol"].to_list()))
    bars = query.minute_bars(
        symbols,
        datetime.combine(day, datetime.min.time()),
        datetime.combine(day, datetime.max.time()),
    )
    arrived = set(bars["symbol"].unique().to_list())
    return [symbol for symbol in symbols if symbol not in arrived]


def converge_late_listing_minutes(
    warehouse: Warehouse,
    day: date,
    *,
    warehouse_path: Path,
    config_path: Path,
    service_runs: Path,
    limit: int,
    timeout: int,
    paper_store: Path = Path("var/workbench/workbench.sqlite3"),
    record_root: Path | None = None,
    work_root: Path = Path("var"),
    win_key: Path = Path("~/.ssh/id_ed25519_winnode"),
    win_script_source: Path = Path("scripts/win_download_minutes.py"),
    run: ServiceRun | None = None,
) -> int:
    """在收盘主数据更新后，对少量晚到上市标的执行一次受控幂等补采。"""
    with record_step(
        run, "晚到上市标的分钟线收敛", interface="Tushare 日线 → QMT 1 分钟线",
        table="minute_bars / bar_minute_1m",
    ) as step:
        missing = late_listing_minute_gaps(warehouse, day)
        if not missing:
            step.detail = "当日上市且有成交的证券分钟线已齐"
            return 0
        if len(missing) > limit:
            raise RuntimeError(
                f"晚到上市标的分钟缺口 {len(missing)} 只，超过自动补采上限 {limit}；"
                "拒绝把一般归档故障当成晚到新股自动重试"
            )

        command = [
            sys.executable, "-u", "-m", "qsys.data.daily_archive_cli",
            "--warehouse", str(warehouse_path),
            "--config", str(config_path),
            "--service-runs", str(service_runs),
            "--record-root", str(record_root or service_runs),
            "--work-root", str(work_root),
            "--paper-store", str(paper_store),
            "--win-key", str(win_key),
            "--win-script-source", str(win_script_source),
            "--lookback-days", "0",
            "--skip-auction",
            "--minute-symbols", ",".join(missing),
            "--minute-timeout", str(max(timeout - 120, 60)),
            "--minute-stall-timeout", str(min(300, max(timeout // 3, 60))),
        ]
        completed = subprocess.run(command, timeout=timeout, check=False)
        if completed.returncode:
            raise RuntimeError(
                f"晚到上市标的分钟补采失败 rc={completed.returncode}：{','.join(missing)}"
            )
        remaining = late_listing_minute_gaps(warehouse, day)
        if remaining:
            raise RuntimeError("补采退出成功但缺口仍存在：" + ",".join(remaining))
        step.rows = len(missing)
        step.detail = f"补采并发布 {len(missing)} 只：{','.join(missing)}"
        return len(missing)


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
            step.warn(f"阻断级指标结果未通过 {blocking} 条，详见服务质检与数据管理 → 指标字典")


def publish_canonical_after_sync(
    provider: TushareProvider,
    warehouse: Warehouse,
    day: date,
    *,
    run: ServiceRun | None = None,
) -> dict[str, int]:
    """legacy 事实和派生完成后发布 canonical；失败必须使同步任务失败。"""
    raw_calendar = provider.trade_cal(day, day, exchange="SSE")
    with record_step(
        run, "canonical 收盘发布", interface="legacy → canonical",
        table="bar_daily / state / reference",
    ) as step:
        result = publish_postclose(warehouse, raw_calendar, day)
        step.rows = sum(result.counts.values())
        step.detail = f"{len(result.reconciled)} 张表写后对账通过"
    return result.counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--day", type=date.fromisoformat, default=None,
                        help="指定交易日；不给则取最近一个已收盘交易日")
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"),
                        help="运行记录目录（服务管理页读取）")
    parser.add_argument("--late-minute-limit", type=int, default=20,
                        help="收盘同步后自动补采晚到上市标的的安全上限")
    parser.add_argument("--late-minute-timeout", type=int, default=900,
                        help="晚到上市标的分钟补采总超时秒数")
    parser.add_argument(
        "--late-minute-paper-store", type=Path,
        default=Path("var/workbench/workbench.sqlite3"),
        help="晚到分钟补采只读的模拟盘成交库",
    )
    parser.add_argument(
        "--late-minute-record-root", type=Path, default=None,
        help="晚到分钟补采运行记录根；默认沿用 --service-runs",
    )
    parser.add_argument(
        "--late-minute-work-root", type=Path, default=Path("var"),
        help="晚到分钟补采的 manifest、下载中转和归档根",
    )
    parser.add_argument(
        "--late-minute-win-key", type=Path,
        default=Path("~/.ssh/id_ed25519_winnode"),
        help="晚到分钟补采使用的 Windows 限权 SSH key",
    )
    parser.add_argument(
        "--late-minute-win-script-source", type=Path,
        default=Path("scripts/win_download_minutes.py"),
        help="随当前制品校验并上传的 Windows 分钟下载器",
    )
    parser.add_argument(
        "--late-minute-convergence", action="store_true",
        help="同步成功后收敛当日晚到上市标的；生产定时任务显式启用",
    )
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
        provider = TushareProvider(client)
        counts = sync(provider, warehouse, day, run=run)
        # 次日开盘前选股要用次日的退市整理期池，今晚一并建好
        next_session = next_open_day(client, day)
        counts.update(refresh_derived(warehouse, day, run=run, next_session=next_session))
        counts.update(publish_canonical_after_sync(
            provider, warehouse, day, run=run
        ))
        if args.late_minute_convergence:
            counts["late_listing_minutes"] = converge_late_listing_minutes(
                warehouse,
                day,
                warehouse_path=args.warehouse,
                config_path=args.config,
                service_runs=args.service_runs,
                limit=args.late_minute_limit,
                timeout=args.late_minute_timeout,
                paper_store=args.late_minute_paper_store,
                record_root=args.late_minute_record_root,
                work_root=args.late_minute_work_root,
                win_key=args.late_minute_win_key,
                win_script_source=args.late_minute_win_script_source,
                run=run,
            )
        after_sync(warehouse, day, ok=True, run=run)
    print(f"=== {day} ===")
    for name, rows in counts.items():
        print(f"  {name:22s} {rows:>9,} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
