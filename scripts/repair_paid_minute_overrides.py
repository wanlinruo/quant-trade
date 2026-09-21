#!/usr/bin/env python3
"""修复付费分钟日期覆盖：粗大偏差标的日移入隔离区，其余按 schema 重写。

只处理 ``paid_minute_bars/year=YYYY/date=YYYYMMDD`` 日期覆盖，不碰年度分片：

1. 与 Tushare 日线对账（``compare_with_daily``），粗大偏差的标的日原样写入
   ``paid_minute_quarantine/year=YYYY/daily-<原因>-mismatch/date=YYYYMMDD``，
   从覆盖中移除——不改值、不填补，由查询层按既定优先级改用下一来源；
2. 其余行按付费分钟 schema（``symbol`` 为 string，含 provider 元数据）重写，
   修正早期发布脚本写成 large_string 的存储契约问题，行内容不变；
3. 每个被改动的文件先原样备份到 ``--backup-dir``，写后回读逐键逐字段核对。

默认只预演并打印计划；``--apply`` 时持有仓库写锁执行。
"""

from __future__ import annotations

import argparse
import json
import shutil
from contextlib import nullcontext
from datetime import date, datetime
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

from qsys.agent.locking import single_instance
from qsys.data import schema
from qsys.data.pipeline.minute_repairs import compare_with_daily, write_paid_minute_file
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

_KEYS = ["symbol", "dt"]


def _day(path: Path) -> date:
    return datetime.strptime(path.parent.name.split("=", 1)[1], "%Y%m%d").date()


def _conforms(path: Path) -> bool:
    actual = pq.read_schema(path)
    expected = schema.PAID_MINUTE_BARS
    return actual.remove_metadata().equals(expected.remove_metadata()) \
        and (actual.metadata or {}).get(b"provider") == expected.metadata[b"provider"]


def _same_rows(left: pl.DataFrame, right: pl.DataFrame) -> bool:
    columns = schema.PAID_MINUTE_BARS.names
    return left.select(columns).sort(_KEYS).equals(right.select(columns).sort(_KEYS))


def plan(warehouse: Path, start: date | None, end: date | None) -> list[dict]:
    files = [
        path for path in sorted((warehouse / "paid_minute_bars").glob(
            "year=*/date=*/part.parquet"))
        if (start is None or _day(path) >= start) and (end is None or _day(path) <= end)
    ]
    if not files:
        return []
    minutes = pl.concat([pl.read_parquet(path) for path in files], how="vertical_relaxed")
    days = minutes["dt"].dt.date()
    daily = WarehouseQuery(Warehouse(warehouse)).tushare_daily(
        sorted(minutes["symbol"].unique().to_list()), days.min(), days.max(),
        columns=("symbol", "dt", "open", "high", "low", "close", "volume", "amount"),
    )
    gross = compare_with_daily(minutes, daily).filter(pl.col("gross").is_not_null())
    items = []
    for path in files:
        day = _day(path)
        bad = gross.filter(pl.col("dt") == day)
        conforms = _conforms(path)
        if bad.is_empty() and conforms:
            continue
        items.append({
            "path": path,
            "day": day,
            "quarantine": dict(bad.select("symbol", "gross").iter_rows()),
            "cast": not conforms,
        })
    return items


def apply(warehouse: Path, backup_dir: Path, item: dict) -> dict:
    path: Path = item["path"]
    relative = path.relative_to(warehouse)
    backup = backup_dir / relative
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)

    frame = pl.read_parquet(path)
    moved = frame.filter(pl.col("symbol").is_in(list(item["quarantine"])))
    keep = frame.filter(~pl.col("symbol").is_in(list(item["quarantine"])))
    stamp = f"{item['day']:%Y%m%d}"
    quarantined = []
    for reason in sorted(set(item["quarantine"].values())):
        symbols = [s for s, r in item["quarantine"].items() if r == reason]
        rows = moved.filter(pl.col("symbol").is_in(symbols))
        target = warehouse / "paid_minute_quarantine" / f"year={item['day'].year}" \
            / f"daily-{reason}-mismatch" / f"date={stamp}" / "part.parquet"
        if target.exists():
            raise FileExistsError(f"quarantine already exists: {target}")
        write_paid_minute_file(rows, target)
        if not _same_rows(pl.read_parquet(target), rows):
            raise RuntimeError(f"quarantine read-back mismatch: {target}")
        quarantined.append({"path": str(target.relative_to(warehouse)),
                            "symbols": sorted(symbols), "rows": rows.height})

    if keep.is_empty():
        path.unlink()
    else:
        write_paid_minute_file(keep, path)
        if not _same_rows(pl.read_parquet(path), keep) or not _conforms(path):
            raise RuntimeError(f"override read-back mismatch: {path}")
    return {
        "path": str(relative),
        "backup": str(backup),
        "rows_before": frame.height,
        "rows_after": keep.height,
        "cast": item["cast"],
        "quarantined": quarantined,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--lock", type=Path, default=Path("var/warehouse-write.lock"),
                        help="与生产定时写任务共用的仓库写锁，仅 --apply 时持有")
    args = parser.parse_args()
    with single_instance(args.lock) if args.apply else nullcontext():
        items = plan(args.warehouse, args.start, args.end)
        summary: dict[str, object] = {
            "operation": "repair_paid_minute_overrides",
            "applied": args.apply,
            "files": len(items),
            "cast_files": sum(1 for item in items if item["cast"]),
            "quarantine_symbol_days": sorted(
                f"{item['day']}:{symbol}:{reason}"
                for item in items for symbol, reason in item["quarantine"].items()
            ),
            "changes": [],
        }
        if args.apply:
            summary["changes"] = [apply(args.warehouse, args.backup_dir, item)
                                  for item in items]
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
