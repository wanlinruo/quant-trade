"""每日归档：日线增量 -> 派生重算 -> 分钟线补齐 -> 导入校验。

**为什么必须每天跑**：QMT 的分钟线只保留约一年且是滚动窗口，实测最早
2025-09-09。每过一天就永久丢一天，漏跑的日子事后补不回来。

在 Linux 上跑，通过 SSH 驱动 Windows 下载。**一个编排者、一份日志、
一处看失败**——两边各自定时是把「多系统拼接脆弱」这个老毛病请回来。

用法：
  uv run python scripts/daily_archive.py                    # 正常每日跑
  uv run python scripts/daily_archive.py --skip-minutes     # 只做日线
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

from qsys.core.calendar import AShareCalendar, last_closed_session
from qsys.data.pipeline.candidates import candidate_stock_days, write_manifest
from qsys.data.pipeline.capital import import_share_capital
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.minutes import import_minute_bars, missing_stock_days
from qsys.data.pipeline.instruments import build_instruments
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.pipeline.valuation import build_valuation
from qsys.data.pipeline.universe import ingest_universe
from qsys.data.providers.akshare_provider import AkshareProvider
from qsys.data.providers.mootdx_provider import MootdxProvider
from qsys.data.quality.minute_rules import check_minute_bars
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger("daily_archive")

# 派生表要多回算一段：首板判定依赖前一日，生态因子依赖昨日涨停状态
_DERIVED_LOOKBACK = 40


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


def main() -> int:
    parser = argparse.ArgumentParser(description="每日数据归档")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--lookback-days", type=int, default=10,
                        help="日线与候选各回看几个日历日，用于补缺口")
    parser.add_argument("--boards", default="main,gem,star")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--skip-minutes", action="store_true")
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

    progress = ingest_universe(
        MootdxProvider(), warehouse, symbols, start, complete_through,
        complete_through=complete_through, batch_size=args.batch_size,
        skip_done=False,  # 每日跑的区间天天不同，跳过判定没有意义
    )
    print(f"日线 {progress.done} 成功 / {progress.failed} 失败")
    if progress.failed:
        log.warning("archive.some_symbols_failed", failed=progress.failed)

    # --- 3. 派生表重算（合并写，不会抹掉同年其余数据）---
    derived_from = complete_through - timedelta(days=_DERIVED_LOOKBACK)
    print(f"涨跌停标记 {build_daily_flags(warehouse, derived_from, complete_through)} 行")
    print(f"生态因子 {build_market_regime(warehouse, derived_from, complete_through)} 行")

    # --- 3b. 股本与估值 ---
    key = str(Path(args.win_key).expanduser())
    ssh = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
           args.win_host]
    stamp = complete_through.strftime("%Y%m%d")

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

    print(f"估值 {build_valuation(warehouse, derived_from, complete_through):,} 行")

    if args.skip_minutes:
        print("按要求跳过分钟线")
        return 0

    # --- 4. 算出还缺哪些股-日的分钟线 ---
    wanted = candidate_stock_days(warehouse, start, complete_through)
    missing = missing_stock_days(warehouse, wanted)
    print(f"候选 {wanted.height} 组，其中缺分钟线 {missing.height} 组")
    if missing.height == 0:
        print("分钟线已齐，无需下载")
        print(f"=== 完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
        return 0

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
             minutes=round((time.time()-started)/60, 1))
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
