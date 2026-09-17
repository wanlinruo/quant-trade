#!/usr/bin/env python3
"""QMT 回补全市场 1 分钟线（D-20）：Windows 只下载，Linux 存储与逐日校验，完成后清理 Windows。

每个交易日：
  1. 当日标的 = Tushare 日线当日全部标的；
  2. Windows 逐只下载（win_download_minutes.py，落盘后才记账）并导出 parquet；
  3. 回传 Linux，按交易日分区导入目标仓库；
  4. 逐日校验（覆盖 / 241 根 / 全天成交量对日线），缺标的重下一次，报告落盘；
  5. 删除 Windows 当日导出目录与这批标的的 QMT 1 分钟缓存。
交易日 08:50–15:50 自动等待，不与行情网关、影子盘和例行归档争用 QMT。
状态文件记录已完成的交易日，中断后重跑从未完成的那天继续。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import structlog

from qsys.data.pipeline.minutes import import_minute_bars
from qsys.data.pipeline.qmt_backfill import in_quiet_hours, verify_day
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger("qmt-minute-backfill")

DAY_BUDGET = timedelta(minutes=30)  # 单日下载预算；开始前要保证这段时间都在非交易时段


def run(cmd: list[str], *, timeout: float = 3600, tries: int = 3) -> str:
    for attempt in range(1, tries + 1):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return result.stdout
        log.warning("step.retry", attempt=attempt, rc=result.returncode,
                    stderr=result.stderr[-300:], cmd=" ".join(cmd[:6]))
        time.sleep(5)
    raise RuntimeError(f"命令失败: {' '.join(cmd[:6])}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--warehouse", type=Path, required=True, help="写入的目标仓库（按交易日分区）")
    p.add_argument("--daily-warehouse", type=Path, required=True, help="读取 tushare_daily 的仓库")
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--work", type=Path, required=True, help="状态、清单、回传与校验报告目录")
    p.add_argument("--max-days", type=int, default=0)
    p.add_argument("--win-host", default="Administrator@118.25.42.149")
    p.add_argument("--win-key", default="~/.ssh/id_ed25519_winnode")
    p.add_argument("--win-python", default=r"C:\qsys\.venv\Scripts\python.exe")
    p.add_argument("--win-download", default=r"C:\qsys\daily\win_download_minutes_d20.py")
    p.add_argument("--win-clear", default=r"C:\qsys\daily\win_clear_minute_cache.py")
    p.add_argument("--win-root", default="C:/qsys/daily/qmt_backfill")
    args = p.parse_args()

    key = str(Path(args.win_key).expanduser())
    ssh = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", args.win_host]
    scp = ["scp", "-i", key, "-o", "BatchMode=yes"]
    target = Warehouse(args.warehouse)
    daily_query = WarehouseQuery(Warehouse(args.daily_warehouse))
    args.work.mkdir(parents=True, exist_ok=True)
    state_path = args.work / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    daily_all = daily_query.tushare_daily(None, args.start, args.end, columns=["symbol", "dt", "volume"])
    sessions = sorted(set(daily_all["dt"].to_list()))
    todo = [d for d in sessions if state.get(str(d), {}).get("status") != "done"]
    print(f"交易日 {len(sessions)} 个，待做 {len(todo)} 个", flush=True)
    done_days = 0
    for day in todo:
        if args.max_days and done_days >= args.max_days:
            break
        while not (in_quiet_hours(datetime.now(), _trading_days(daily_query))
                   and in_quiet_hours(datetime.now() + DAY_BUDGET, _trading_days(daily_query))):
            log.info("wait.trading_hours", day=str(day))
            time.sleep(300)
        started = time.time()
        daily = daily_all.filter(pl.col("dt") == day).select("symbol", "volume")
        stamp = f"{day:%Y%m%d}"
        day_work = args.work / stamp
        incoming = day_work / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        manifest = day_work / "manifest.csv"
        daily.select("symbol", pl.lit(day).alias("dt")).write_csv(manifest)
        day_root = f"{args.win_root}/{stamp}"

        def download(manifest_path: Path, sub: str) -> None:
            # 每轮一个子目录：下载器的续传账本会把「返回空」的标的也记为已做，重试必须换账本
            win_dir = f"{day_root}/{sub}"
            run(ssh + [f"New-Item -ItemType Directory -Force -Path {win_dir} | Out-Null"])
            run(scp + [str(manifest_path), f"{args.win_host}:{win_dir}/manifest.csv"])
            out = run(ssh + [
                f'$env:PYTHONIOENCODING="utf-8"; {args.win_python} -u {args.win_download} '
                f'--manifest {win_dir}/manifest.csv --out {win_dir} '
                f'--window-start {stamp} --window-end {stamp} --sleep 0 --flush-every 200 2>&1; '
                'exit $LASTEXITCODE'], timeout=3 * 3600, tries=1)
            print("  " + " | ".join(out.strip().splitlines()[-2:]), flush=True)
            names = run(ssh + [f"Get-ChildItem {win_dir}/*.parquet -ErrorAction SilentlyContinue "
                               "| ForEach-Object { $_.Name }"]).split()
            for name in names:
                local = f"{sub}-{name}"
                if not (incoming / local).exists() and not (day_work / "imported" / local).exists():
                    run(scp + [f"{args.win_host}:{win_dir}/{name}", str(incoming / local)])
            result = import_minute_bars(target, incoming, archive_dir=day_work / "imported")
            if result.errors:
                raise RuntimeError(f"{day} 导入有 {result.errors} 个坏文件")

        def verify() -> dict:
            path = target.day_path("minute_bars", day)
            bars = pl.read_parquet(path).select("symbol", "dt", "volume") if path.exists() \
                else pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Datetime("us"), "volume": pl.Int64})
            return verify_day(day, bars, daily)

        try:
            download(manifest, "main")
            report = verify()
            if report["missing_symbols"]:
                retry = day_work / "manifest_retry.csv"
                pl.DataFrame({"symbol": report["missing_symbols"], "dt": [day] * len(report["missing_symbols"])}) \
                    .write_csv(retry)
                download(retry, "retry")
                report = verify() | {"retried": len(report["missing_symbols"])}
        except Exception as exc:
            log.error("day.failed", day=str(day), error=str(exc))
            state[str(day)] = {"status": "failed", "error": str(exc), "at": datetime.now().isoformat()}
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1))
            continue

        (day_work / "verify.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        # 数据已在 Linux 入库并完成校验：清理 Windows 临时导出与这批标的的 QMT 1 分钟缓存
        run(scp + [str(manifest), f"{args.win_host}:{day_root}/symbols.csv"])
        cleared = run(ssh + [f'{args.win_python} {args.win_clear} --symbols {day_root}/symbols.csv'])
        run(ssh + [f"Remove-Item -Recurse -Force {day_root}"])
        state[str(day)] = {
            "status": "done", "rows": report["rows"], "symbols": report["symbols_with_bars"],
            "expected": report["symbols_expected"], "missing": len(report["missing_symbols"]),
            "non_241": report["non_241"], "volume_mismatch": report["volume_mismatch"],
            "ok": report["ok"], "seconds": round(time.time() - started), "cache": cleared.strip(),
            "at": datetime.now().isoformat(timespec="seconds"),
        }
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        print(f"{day} 完成 rows={report['rows']:,} symbols={report['symbols_with_bars']}/"
              f"{report['symbols_expected']} missing={len(report['missing_symbols'])} "
              f"non_241={report['non_241']} vol_mismatch={report['volume_mismatch']} "
              f"{round(time.time() - started)}s | {cleared.strip()}", flush=True)
        done_days += 1
    return 0


_TRADING_DAYS: set[date] | None = None


def _trading_days(query: WarehouseQuery) -> set[date]:
    """交易日判断用 Tushare 日线的已知交易日加当天（当天若是工作日视为交易日，保守等待）。"""
    global _TRADING_DAYS
    if _TRADING_DAYS is None:
        today = date.today()
        frame = query.tushare_daily(None, today - timedelta(days=30), today, columns=["dt"])
        _TRADING_DAYS = set(frame["dt"].to_list())
    today = date.today()
    return _TRADING_DAYS | ({today} if today.weekday() < 5 else set())


if __name__ == "__main__":
    sys.exit(main())
