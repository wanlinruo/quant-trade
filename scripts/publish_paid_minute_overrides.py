#!/usr/bin/env python3
"""为 QMT 零价格标的发布来源明确的付费分钟覆盖。

只读取 QMT 隔离仓库与付费暂存区。默认预演；传 ``--apply`` 后才把通过完整
241 时刻网格、数值校验且与 Tushare 日线无粗大偏差的标的日写入目标仓库的
``paid_minute_bars``。已有文件覆盖前必须复制到指定备份目录，写入采用临时
文件原子替换，并保留付费源 schema 元数据。写入全程持有仓库写锁。
"""

from __future__ import annotations

import argparse
import json
import shutil
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

import polars as pl

from qsys.agent.locking import single_instance
from qsys.data.pipeline.minute_repairs import (
    compare_with_daily,
    invalid_qmt_price_symbols,
    valid_paid_overrides,
    write_paid_minute_file,
)
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--paid-staging", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--daily-warehouse", type=Path,
                        help="提供 tushare_daily 对账的仓库，默认同 --warehouse")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--lock", type=Path, default=Path("var/warehouse-write.lock"),
                        help="与生产定时写任务共用的仓库写锁，仅 --apply 时持有")
    args = parser.parse_args()
    with single_instance(args.lock) if args.apply else nullcontext():
        return _run(args)


def _run(args: argparse.Namespace) -> int:
    daily_query = WarehouseQuery(Warehouse(args.daily_warehouse or args.warehouse))
    qmt_root = args.warehouse / "minute_bars"
    paid_target = args.warehouse / "paid_minute_bars"
    summary = {
        "operation": "publish_paid_minute_overrides",
        "applied": args.apply,
        "days_with_qmt_zero_prices": 0,
        "qmt_invalid_symbols": 0,
        "accepted_symbols": 0,
        "accepted_rows": 0,
        "rejected": {},
        "written_days": [],
    }
    for qmt_path in sorted(qmt_root.glob("year=*/date=*/part.parquet")):
        stamp = qmt_path.parent.name.split("=", 1)[1]
        day = datetime.strptime(stamp, "%Y%m%d").date()
        qmt = pl.read_parquet(qmt_path)
        invalid = invalid_qmt_price_symbols(qmt)
        if not invalid:
            continue
        summary["days_with_qmt_zero_prices"] += 1
        summary["qmt_invalid_symbols"] += len(invalid)
        source = args.paid_staging / f"year={day.year}" / f"date={stamp}" / "part.parquet"
        if source.exists():
            accepted, rejected = valid_paid_overrides(pl.read_parquet(source), invalid, day)
        else:
            accepted = pl.DataFrame()
            rejected = {symbol: "missing_partition" for symbol in invalid}
        if not accepted.is_empty():
            daily = daily_query.tushare_daily(
                sorted(accepted["symbol"].unique().to_list()), day, day,
                columns=("symbol", "dt", "open", "high", "low", "close", "volume", "amount"),
            )
            gross = compare_with_daily(accepted, daily).filter(pl.col("gross").is_not_null())
            for symbol, reason in gross.select("symbol", "gross").iter_rows():
                rejected[symbol] = f"daily_{reason}"
            accepted = accepted.filter(~pl.col("symbol").is_in(gross["symbol"].to_list()))
        for reason in rejected.values():
            summary["rejected"][reason] = summary["rejected"].get(reason, 0) + 1
        if accepted.is_empty():
            continue
        summary["accepted_symbols"] += accepted["symbol"].n_unique()
        summary["accepted_rows"] += accepted.height
        summary["written_days"].append(str(day))
        if not args.apply:
            continue
        target = paid_target / f"year={day.year}" / f"date={stamp}" / "part.parquet"
        if target.exists():
            backup = args.backup_dir / f"year={day.year}" / f"date={stamp}" / "part.parquet"
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                raise FileExistsError(f"backup already exists: {backup}")
            shutil.copy2(target, backup)
            accepted = pl.concat([pl.read_parquet(target), accepted], how="vertical") \
                .unique(subset=["symbol", "dt"], keep="last").sort(["symbol", "dt"])
        write_paid_minute_file(accepted, target)

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
