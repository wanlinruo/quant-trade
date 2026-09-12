"""每日归档：竞价 tick -> 日线增量 -> 派生重算 -> 分钟线补齐 -> 导入校验。

**为什么必须每天跑**：两份数据都是滚动窗口，漏跑的日子事后补不回来。
  - 分钟线：约一年，实测最早 2025-09-09
  - **竞价 tick：只有约 20 个交易日**（实测边界 2026-08-10 / 锚点 2026-09-08）
    ——这条比分钟线急一个数量级，漏三周就全丢。

在 Linux 上跑，通过 SSH 驱动 Windows 下载。**一个编排者、一份日志、
一处看失败**——两边各自定时是把「多系统拼接脆弱」这个老毛病请回来。

用法：
  uv run python scripts/daily_archive.py                    # 正常每日跑
  uv run python scripts/daily_archive.py --skip-minutes     # 只做日线
  uv run python scripts/daily_archive.py --lookback-days 30 # 补几天的缺口

日线增量有自己的时间预算（--daily-budget-minutes），到点就收工去做分钟线：
日线明天还能补，分钟线和竞价 tick 是滚动窗口，漏了就没了。
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

from qsys.core.calendar import AShareCalendar, last_closed_session
from qsys.data.pipeline.auction import (
    import_auction_ticks,
    missing_auction_days,
)
from qsys.data.pipeline.candidates import candidate_stock_days, write_manifest
from qsys.data.pipeline.capital import import_share_capital
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.index_bars import CSI300, ingest_index_bars
from qsys.data.pipeline.minutes import import_minute_bars, missing_stock_days
from qsys.data.pipeline.instruments import build_instruments
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.pipeline.valuation import build_valuation
from qsys.data.pipeline.universe import ingest_universe, source_is_serving
from qsys.data.providers.akshare_provider import AkshareProvider
from qsys.data.providers.mootdx_provider import MootdxProvider
from qsys.data.quality.minute_rules import check_minute_bars
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger("daily_archive")

# 派生表要多回算一段：首板判定依赖前一日，生态因子依赖昨日涨停状态
_DERIVED_LOOKBACK = 40

# 上游探针。挑三只跨市场的高流动性标的：它们不会在同一个 8 天窗口里同时整段
# 停牌，所以「三只全空」只可能是上游不给数据了。
_PROBE_SYMBOLS = ("000001.SZ", "600000.SH", "600519.SH")


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


def _archive_auction(args, warehouse, wanted, ssh, key, stamp) -> None:
    """竞价 tick 归档。失败**不中断**整个归档——日线与分钟线更要紧。"""
    missing = missing_auction_days(warehouse, wanted)
    print(f"竞价 tick：候选 {wanted.height} 组，其中缺 {missing.height} 组")
    if missing.height == 0:
        print("竞价 tick 已齐")
        return

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
        print(f"  [竞价 tick 失败] {exc}——继续做分钟线")
        return

    result = import_auction_ticks(
        warehouse, local, archive_dir=Path("var/auction-done")
    )
    print(f"竞价 tick 导入 {result.files} 文件 / {result.rows:,} 笔 / "
          f"{result.errors} 错误")


def main() -> int:
    parser = argparse.ArgumentParser(description="每日数据归档")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--lookback-days", type=int, default=10,
                        help="日线与候选各回看几个日历日，用于补缺口")
    parser.add_argument("--boards", default="main,gem,star")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--daily-budget-minutes", type=float, default=75.0,
                        help="日线增量的时间预算。到点就收工去做分钟线——"
                             "日线明天能补，滚动窗口的数据补不回来")
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
    args = parser.parse_args()

    warehouse = Warehouse(Path(args.warehouse))
    reference = AkshareProvider()
    today = date.today()
    started = time.time()

    # --- 1. 交易日历，确定采到哪天 ---
    sessions = reference.fetch_sessions(today - timedelta(days=90), today)
    calendar = AShareCalendar(sessions)
    complete_through = last_closed_session(calendar, datetime.now())
    if complete_through is None:
        log.warning("archive.no_closed_session")
        print("没有已收盘的交易日，退出")
        return 0
    start = complete_through - timedelta(days=args.lookback_days)
    print(f"=== 归档至 {complete_through}（回看 {args.lookback_days} 天）===")

    # --- 2. 标的主表与日线增量 ---
    print(f"标的主表 {build_instruments(reference, warehouse)} 只")
    instruments = WarehouseQuery(warehouse).instruments()
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
    if not args.skip_auction:
        _archive_auction(
            args, warehouse,
            candidate_stock_days(warehouse, start, complete_through,
                                 sessions=sessions),
            ssh, key, stamp,
        )
    if args.only_auction:
        print(f"=== 只做竞价 tick，完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
        return 0

    # --- 2a. 先探上游是否还在供数 ---
    # 2026-09-10/11 两天的教训：mootdx 的 K 线接口失效后**不报错**，每次调用等
    # 3.8 秒返回空表。全市场 5219 只要跑 5.5 小时，systemd 在 2 小时处杀掉，
    # 后面的分钟线一步都没做。探针几秒钟就能把这种情况认出来。
    provider = MootdxProvider()
    probes = [s for s in _PROBE_SYMBOLS if s in set(symbols)] or symbols[:3]
    daily_source_up = source_is_serving(provider, probes, start, complete_through)

    if not daily_source_up:
        log.error("archive.daily_source_down", probes=probes,
                  hint="探针标的全部返回空——高流动性标的不会同时整段停牌")
        print(f"[日线源失效] 探针 {probes} 全部取不到数据。")
        print("  跳过日线、指数与派生表——跑下去只会用 3.8 秒/只换一批空表，")
        print("  并把整个归档拖到超时，连后面的分钟线都做不成。")
    else:
        progress = ingest_universe(
            provider, warehouse, symbols, start, complete_through,
            complete_through=complete_through, batch_size=args.batch_size,
            # 区间键含起止日期，换一天自然全采；同一天重跑才跳过已完成的，
            # 这是被超时杀掉之后能续上的前提。
            skip_done=True,
            deadline=time.monotonic() + args.daily_budget_minutes * 60,
        )
        print(f"日线 {progress.done} 有数据 / {progress.empty} 空 / "
              f"{progress.failed} 失败")
        if progress.remaining > 0:
            daily_source_up = False
            log.warning("archive.daily_budget_exhausted",
                        remaining=progress.remaining)
            print(f"  [预算用尽] 还剩 {progress.remaining} 只没采，"
                  f"先去做滚动窗口的数据；重跑本脚本会从断点续上")
        if progress.failed:
            log.warning("archive.some_symbols_failed", failed=progress.failed)
        if progress.empty > progress.done:
            # 探针过了但大面积返回空：上游在跑的过程中挂了。宁可当失败处理，
            # 也不要让派生表基于半截日线重算。
            daily_source_up = False
            log.error("archive.daily_mostly_empty",
                      done=progress.done, empty=progress.empty)
            print(f"  [警告] 空结果多于有数据的（{progress.empty} > {progress.done}），"
                  f"按上游失效处理，不重算派生表")

    if daily_source_up:
        # --- 2b. 指数日线（S6 Gate 的 index_ma20_slope_5d 要用）---
        # 指数走 mootdx 的 index()，个股接口取不到
        index_rows = ingest_index_bars(
            provider, warehouse, [CSI300], start, complete_through,
            complete_through=complete_through,
        )
        print(f"指数日线 {index_rows} 行")

        # --- 3. 派生表重算（合并写，不会抹掉同年其余数据）---
        derived_from = complete_through - timedelta(days=_DERIVED_LOOKBACK)
        print(f"涨跌停标记 {build_daily_flags(warehouse, derived_from, complete_through)} 行")
        print(f"生态因子 {build_market_regime(warehouse, derived_from, complete_through)} 行")

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

    if daily_source_up:
        print(f"估值 {build_valuation(warehouse, derived_from, complete_through):,} 行")
    else:
        print("跳过估值重算——它依赖当日日线")

    wanted = candidate_stock_days(warehouse, start, complete_through)

    if args.skip_minutes:
        print("按要求跳过分钟线")
        print(f"=== 完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
        return 0

    # --- 5. 算出还缺哪些股-日的分钟线 ---
    missing = missing_stock_days(warehouse, wanted)
    print(f"候选 {wanted.height} 组，其中缺分钟线 {missing.height} 组")
    if missing.height == 0:
        if daily_source_up:
            print("分钟线已齐，无需下载")
        else:
            # 候选由 daily_flags 的首板推出，daily_flags 又派生自当日日线。
            # 日线没进来，候选就必然为空——这不是「已齐」，是算不出来。
            print("[候选算不出] daily_flags 缺当日日线，候选为空——"
                  "这不等于分钟线已齐，补上日线源后必须重跑")
        print(f"=== 完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
        return 0 if daily_source_up else 1

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

    print(f"=== 完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
    log.info("archive.done", days=len(days), rows=result.rows, issues=issues,
             daily_source_up=daily_source_up,
             minutes=round((time.time()-started)/60, 1))
    return 1 if (issues or not daily_source_up) else 0


if __name__ == "__main__":
    sys.exit(main())
