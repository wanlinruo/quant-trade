"""每日归档：竞价 tick -> 股本 -> 分钟线补齐 -> 导入校验。

**本脚本只做 QMT 链路**（竞价 tick、股本、分钟线），它们都来自 Windows 上的
QMT，Tushare 给不了。日线、指数与四张派生表（adj_factors / daily_flags /
market_regime / valuation_snapshot）自 2026-09-14 起全部归 `tushare_sync`，
由 `qsys-tushare-sync.timer` 每天 17:00 与 19:00 驱动。

2026-09-15 删掉了这里的 mootdx 日线分支：mootdx 的 K 线接口 2026-09-10 起
对所有服务器返回空，探针每天如实报告「源失效」，脚本据此 `return 1`——
于是这个服务从换源那天起天天亮红灯，而它的活其实干得好好的。红灯天天亮
就不再有信息量：`issues` 真的不为 0 的那天，systemd 报的还是同一个
exit-code，分辨不出来。现在退出码只反映 `issues`。

**为什么必须每天跑**：两份数据都是滚动窗口，漏跑的日子事后补不回来。
  - 分钟线：约一年，实测最早 2025-09-09
  - **竞价 tick：只有约 20 个交易日**（实测边界 2026-08-10 / 锚点 2026-09-08）
    ——这条比分钟线急一个数量级，漏三周就全丢。

在 Linux 上跑，通过 SSH 驱动 Windows 下载。**一个编排者、一份日志、
一处看失败**——两边各自定时是把「多系统拼接脆弱」这个老毛病请回来。

用法：
  uv run python scripts/daily_archive.py                    # 正常每日跑
  uv run python scripts/daily_archive.py --only-auction     # 只做竞价 tick
  uv run python scripts/daily_archive.py --lookback-days 30 # 补几天的缺口
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import structlog

from qsys.core.calendar import last_closed_session
from qsys.data.pipeline.auction import (
    import_auction_ticks,
    missing_auction_days,
)
from qsys.data.pipeline.candidates import candidate_stock_days, write_manifest
from qsys.data.pipeline.capital import import_share_capital
from qsys.data.pipeline.minutes import import_minute_bars, missing_stock_days
from qsys.data.pipeline.canonical_publish import publish_minutes
from qsys.data.quality.minute_rules import check_minute_bars
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.ops.services import FAILED, SKIPPED, SUCCEEDED, ServiceRun
from qsys.paper.calendar import load_trading_calendar

log = structlog.get_logger("daily_archive")


def run(cmd: list[str], *, what: str, timeout: int = 3600) -> str:
    """跑一条外部命令，失败就抛。"""
    started = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    elapsed = time.time() - started
    if result.returncode != 0:
        log.error(
            "step.failed", step=what, rc=result.returncode,
            stderr=result.stderr[-2000:], seconds=round(elapsed, 1),
        )
        raise RuntimeError(f"{what} 失败 (rc={result.returncode})")
    log.info("step.ok", step=what, seconds=round(elapsed, 1))
    return result.stdout


def _exit_code(rc: int, *, auction_ok: bool) -> int:
    """竞价失败时不中断后续步骤，但最终必须让 systemd 看到失败（D-7）。"""
    return rc or (0 if auction_ok else 1)


def _publish_canonical_minutes(
    warehouse: Warehouse,
    start: date,
    end: date,
    service_run: ServiceRun,
) -> int:
    """发布归档涉及的 canonical 分钟分区；失败向上抛出。"""
    with service_run.step(
        "canonical 分钟线", interface="分钟来源 → bar_minute_1m",
        table="bar_minute_1m",
    ) as step:
        result = publish_minutes(warehouse, start, end)
        rows = result.counts["bar_minute_1m"]
        step.rows = rows
        step.detail = f"{start}..{end} 写后逐键逐字段对账通过"
        return rows


def _archive_auction(args, warehouse, wanted, ssh, key, stamp) -> bool:
    """竞价 tick 归档，返回是否成功。

    失败**不中断**整个归档——分钟线照做；但结果要带回 main 决定退出码。
    QMT 只留约 20 个交易日，吞掉失败等于让数据悄悄过期。
    """
    missing = missing_auction_days(warehouse, wanted)
    print(f"竞价 tick：候选 {wanted.height} 组，其中缺 {missing.height} 组")
    if missing.height == 0:
        print("竞价 tick 已齐")
        return True

    local = Path("var/auction-incoming")
    local.mkdir(parents=True, exist_ok=True)
    manifest = Path("var") / f"auction_manifest_{stamp}.csv"
    write_manifest(missing, manifest)

    win_dir = f"{args.win_root}/auction_{stamp}"
    try:
        run(ssh + [f"New-Item -ItemType Directory -Force -Path {win_dir} | Out-Null"],
            what="建竞价目录")
        run(["scp", "-i", key, "-o", "BatchMode=yes", str(manifest),
             f"{args.win_host}:{win_dir}/manifest.csv"], what="上传竞价清单")
        output = run(ssh + [
            f'$env:PYTHONIOENCODING="utf-8"; {args.win_python} -u '
            f'{args.win_auction_script} --manifest {win_dir}\\manifest.csv '
            f'--out {win_dir} --sleep 0.1'
        ], what="Windows 下载竞价 tick", timeout=7200)
        for line in output.strip().splitlines()[-3:]:
            print("  " + line)
        run(["scp", "-i", key, "-o", "BatchMode=yes",
             f"{args.win_host}:{win_dir}/*.parquet", str(local)],
            what="回传竞价 parquet")
    except Exception as exc:
        log.error("archive.auction_failed", error=str(exc))
        print(f"  [竞价 tick 失败] {exc}——继续做分钟线，最终以失败退出")
        return False

    result = import_auction_ticks(
        warehouse, local, archive_dir=Path("var/auction-done")
    )
    print(f"竞价 tick 导入 {result.files} 文件 / {result.rows:,} 笔 / "
          f"{result.errors} 错误")
    if result.errors:
        log.error("archive.auction_import_errors", errors=result.errors)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="每日数据归档")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--lookback-days", type=int, default=10,
                        help="日线与候选各回看几个日历日，用于补缺口")
    parser.add_argument("--boards", default="main,gem,star")
    parser.add_argument("--skip-minutes", action="store_true")
    parser.add_argument("--skip-auction", action="store_true")
    parser.add_argument("--only-auction", action="store_true",
                        help="只做竞价 tick。窗口只有 20 天，出问题要能单独重试")
    parser.add_argument("--win-auction-script",
                        default=r"C:\qsys\win_download_auction_ticks.py")
    parser.add_argument("--skip-capital", action="store_true")
    parser.add_argument("--win-capital-script", default=r"C:\qsys\win_download_capital.py")
    parser.add_argument("--win-host", default="Administrator@118.25.42.149")
    parser.add_argument("--win-key", default="~/.ssh/id_ed25519_winnode")
    parser.add_argument("--win-python", default=r"C:\qsys\.venv\Scripts\python.exe")
    parser.add_argument("--win-script", default=r"C:\qsys\win_download_minutes.py")
    parser.add_argument("--win-root", default="C:/qsys/daily")
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--service-runs", default="var/service-runs",
                        help="运行记录目录（服务管理页读取）")
    args = parser.parse_args()

    with ServiceRun.open(Path(args.service_runs), "daily-archive") as service_run:
        return _archive(args, service_run)


def _archive(args, service_run: ServiceRun) -> int:
    warehouse = Warehouse(Path(args.warehouse))
    today = date.today()
    started = time.time()

    # --- 1. 交易日历，确定采到哪天。与其他任务同源：Tushare trade_cal ---
    with service_run.step("确定交易日", interface="Tushare trade_cal") as step:
        calendar = load_trading_calendar(args.config)
        sessions = calendar.sessions_in_range(today - timedelta(days=90), today)
        complete_through = last_closed_session(calendar, datetime.now())
        step.detail = f"归档至 {complete_through}" if complete_through else "没有已收盘的交易日"
    if complete_through is None:
        log.warning("archive.no_closed_session")
        service_run.skip("没有已收盘的交易日")
        print("没有已收盘的交易日，退出")
        return 0
    service_run.session = complete_through
    start = complete_through - timedelta(days=args.lookback_days)
    print(f"=== 归档至 {complete_through}（回看 {args.lookback_days} 天）===")

    # --- 2. 标的主表与日线增量 ---
    # instruments 已由 16:30 的 Tushare 同步维护（含退市股）。这里若继续调用
    # AkShare，会在每天 15:40 把真值表覆盖回不含退市股的旧源。
    instruments = WarehouseQuery(warehouse).instruments()
    if instruments.height == 0:
        raise RuntimeError("instruments 为空；先恢复 qsys-tushare-sync，不回退 AkShare")
    print(f"标的主表 {instruments.height} 只（Tushare 已落库）")
    boards = {b.strip() for b in args.boards.split(",") if b.strip()}
    alive = instruments.filter(
        pl.col("board").is_in(list(boards))
        & (pl.col("delist_date").is_null() | (pl.col("delist_date") >= start))
    )
    symbols = alive["symbol"].to_list()

    # --- 1b. 竞价 tick：**最先做**。窗口只有约 20 个交易日，而全市场日线
    # 增量要跑 5 个多小时——压在后面等于拿不可恢复的数据去赌可恢复的数据。
    # 此时 daily_flags 还没有今天，故显式传入交易日历。
    key = str(Path(args.win_key).expanduser())
    ssh = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
           args.win_host]
    stamp = complete_through.strftime("%Y%m%d")
    auction_ok = True
    if not args.skip_auction:
        with service_run.step("竞价逐笔（首板次日）", interface="QMT 竞价逐笔（Windows）",
                              table="auction_ticks") as step:
            auction_ok = _archive_auction(
                args, warehouse,
                candidate_stock_days(warehouse, start, complete_through,
                                     sessions=sessions),
                ssh, key, stamp,
            )
            if not auction_ok:
                step.fail("竞价采集或导入失败；QMT 只留约 20 个交易日，需尽快补采")
    if args.only_auction:
        print(f"=== 只做竞价 tick，完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
        return _exit_code(0, auction_ok=auction_ok)

    # --- 3b. 股本与估值 ---
    if not args.skip_capital:
        # 股本变动是事件驱动的，全市场重下只要约 5 分钟，比算增量更省事
        cap_dir = f"{args.win_root}/capital_{stamp}"
        cap_local = Path("var/capital-incoming")
        cap_local.mkdir(parents=True, exist_ok=True)
        symbols_csv = Path("var/symbols.csv")
        instruments.select("symbol").write_csv(symbols_csv)

        run(ssh + [f"New-Item -ItemType Directory -Force -Path {cap_dir} | Out-Null"],
            what="建股本目录")
        run(["scp", "-i", key, "-o", "BatchMode=yes", str(symbols_csv),
             f"{args.win_host}:{cap_dir}/symbols.csv"], what="上传标的清单")
        run(ssh + [
            f'$env:PYTHONIOENCODING="utf-8"; {args.win_python} -u '
            f'{args.win_capital_script} --symbols {cap_dir}\\symbols.csv '
            f'--out {cap_dir}'
        ], what="Windows 下载股本", timeout=3600)
        run(["scp", "-i", key, "-o", "BatchMode=yes",
             f"{args.win_host}:{cap_dir}/*.parquet", str(cap_local)],
            what="回传股本")
        cap_result = import_share_capital(
            warehouse, cap_local, archive_dir=Path("var/capital-done")
        )
        print(f"股本 {cap_result.files} 文件 / {cap_result.rows:,} 行 / "
              f"{cap_result.errors} 错误")

    # 与竞价同样显式传入交易日历：15:40 时 daily_flags 还没有今天，不传的话
    # 昨日首板找不到「下一个交易日」，今天的候选分钟线要拖到明天才下（服务质检发现）
    wanted = candidate_stock_days(warehouse, start, complete_through, sessions=sessions)

    if args.skip_minutes:
        service_run.add_step("分钟线", status=SKIPPED, detail="按参数跳过")
        print("按要求跳过分钟线")
        print(f"=== 完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
        return _exit_code(0, auction_ok=auction_ok)

    # --- 5. 算出还缺哪些股-日的分钟线 ---
    missing = missing_stock_days(warehouse, wanted)
    print(f"候选 {wanted.height} 组，其中缺分钟线 {missing.height} 组")
    minutes_started = datetime.now()
    if missing.height == 0:
        service_run.add_step("分钟线（首板次日候选）", status=SUCCEEDED,
                             interface="QMT 1 分钟线（Windows）", table="minute_bars", rows=0,
                             detail=f"候选 {wanted.height} 组，均已到货")
        # 候选由 daily_flags 的首板推出，而 daily_flags 现在由 tushare_sync
        # 维护。本脚本 15:41 跑、同步 17:00 才跑，所以这里读到的是**昨日**
        # 收盘后那次同步的结果——候选为空是正常的「已齐」，不是算不出来。
        print("分钟线已齐，无需下载")
        _publish_canonical_minutes(
            warehouse, complete_through, complete_through, service_run
        )
        print(f"=== 完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
        return _exit_code(0, auction_ok=auction_ok)

    local_dir = Path("var/minutes-incoming")
    local_dir.mkdir(parents=True, exist_ok=True)
    manifest = Path("var") / f"manifest_{stamp}.csv"
    write_manifest(missing, manifest)

    win_dir = f"{args.win_root}/{stamp}"
    win_manifest = f"{win_dir}/manifest.csv"

    # --- 5. 驱动 Windows 下载 ---
    run(ssh + [f"New-Item -ItemType Directory -Force -Path {win_dir} | Out-Null"],
        what="建 Windows 目录")
    run(["scp", "-i", key, "-o", "BatchMode=yes", str(manifest),
         f"{args.win_host}:{win_manifest}"], what="上传清单")

    lo, hi = min(missing["dt"].to_list()), max(missing["dt"].to_list())
    download = (
        f'$env:PYTHONIOENCODING="utf-8"; {args.win_python} -u {args.win_script} '
        f'--manifest {win_dir}\\manifest.csv --out {win_dir} '
        f'--window-start {lo.strftime("%Y%m%d")} --window-end {hi.strftime("%Y%m%d")} '
        f'--sleep 0.1'
    )
    output = run(ssh + [download], what="Windows 下载分钟线", timeout=7200)
    for line in output.strip().splitlines()[-4:]:
        print("  " + line)

    # --- 6. 取回并导入 ---
    run(["scp", "-i", key, "-o", "BatchMode=yes",
         f"{args.win_host}:{win_dir}/*.parquet", str(local_dir)],
        what="回传 parquet")
    result = import_minute_bars(
        warehouse, local_dir, archive_dir=Path("var/minutes-done")
    )
    print(f"导入 {result.files} 文件 / {result.rows:,} 行 / {result.errors} 错误")
    service_run.add_step(
        "分钟线（首板次日候选）", status=FAILED if result.errors else SUCCEEDED,
        interface="QMT 1 分钟线（Windows）", table="minute_bars", rows=result.rows,
        started_at=minutes_started,
        detail=f"候选 {wanted.height} 组，缺 {missing.height} 组；导入 {result.files} 个文件、"
               f"{result.errors} 个坏文件",
    )
    if result.errors:
        log.warning("archive.import_errors", errors=result.errors)

    # --- 7. 跨源校验 ---
    days = sorted({d for d in missing["dt"].to_list()})
    issues = 0
    for day in days:
        found = check_minute_bars(warehouse, day)
        issues += len(found)
        for issue in found[:3]:
            print(f"  [质量] {day} {issue.symbol} {issue.rule}: {issue.detail}")
    print(f"跨源校验 {len(days)} 天，问题 {issues} 个")
    service_run.add_step("分钟线校验", status=FAILED if issues else SUCCEEDED,
                         interface="仓库内规则", table="minute_bars",
                         detail=f"校验 {len(days)} 天，问题 {issues} 个")

    if not result.errors and not issues:
        _publish_canonical_minutes(
            warehouse,
            min(days) if days else complete_through,
            max(days) if days else complete_through,
            service_run,
        )

    print(f"=== 完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
    log.info("archive.done", days=len(days), rows=result.rows, issues=issues,
             minutes=round((time.time()-started)/60, 1))
    return _exit_code(1 if issues else 0, auction_ok=auction_ok)


if __name__ == "__main__":
    sys.exit(main())
