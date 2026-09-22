#!/usr/bin/env python3
"""把漏掉的 Tushare/聚宽覆盖标的日补进已发布的规范一分钟线。

只追加，不重建：对每个覆盖日期，

1. 找出覆盖数据集里、却不在已发布 ``bar_minute_1m`` 分区里的标的；
2. 用现行选源逻辑（``canonical_minute_frame``）取这些标的，只接受来源为覆盖本身的
   整个标的日（选到付费源或 QMT 的一律不动、只报告）；
3. 已发布分区原有的每一行保持不变，新分区 = 原分区 + 补入行；
4. 改动前把原分区原样备份到 ``--backup-dir``，写后回读核对：原有行逐键逐字段不变，
   新增行等于补入行，并通过规范表的写入契约校验。

不整日重建，是因为 2025 年 QMT 旧数据含停牌占位（全天零成交、平价、无日线），
整日重建会把它们带进规范表（审计文档 12.6）。

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

from qsys.agent.locking import single_instance
from qsys.data import schema
from qsys.data.pipeline.canonical_minute import _validate, canonical_minute_frame
from qsys.data.storage import Warehouse

OVERRIDES = {"tushare_minute_bars": "tushare", "joinquant_minute_bars": "joinquant"}
_KEYS = ["symbol", "ts"]


def _days(warehouse: Path, start: date | None, end: date | None) -> list[date]:
    days = set()
    for dataset in OVERRIDES:
        for path in (warehouse / dataset).glob("year=*/date=*/part.parquet"):
            day = datetime.strptime(path.parent.name.split("=", 1)[1], "%Y%m%d").date()
            if (start is None or day >= start) and (end is None or day <= end):
                days.add(day)
    return sorted(days)


def _canonical_path(warehouse: Path, day: date) -> Path:
    return warehouse / "bar_minute_1m" / f"year={day.year}" / f"date={day:%Y%m%d}" \
        / "part.parquet"


def plan_day(warehouse: Path, day: date) -> dict | None:
    wanted: dict[str, str] = {}
    for dataset, source in OVERRIDES.items():
        path = warehouse / dataset / f"year={day.year}" / f"date={day:%Y%m%d}" / "part.parquet"
        if path.exists():
            for symbol in pl.read_parquet(path, columns=["symbol"])["symbol"].unique().to_list():
                wanted.setdefault(symbol, source)
    target = _canonical_path(warehouse, day)
    published = pl.read_parquet(target, columns=["symbol"])["symbol"].unique().to_list() \
        if target.exists() else []
    missing = sorted(set(wanted) - set(published))
    if not missing:
        return None
    selected = canonical_minute_frame(Warehouse(warehouse), day).filter(
        pl.col("symbol").is_in(missing)
    )
    sources = dict(selected.group_by("symbol").agg(pl.col("source").first()).iter_rows())
    add = [s for s in missing if sources.get(s) == wanted[s]]
    skipped = {s: sources.get(s) for s in missing if s not in add}
    return {
        "day": day,
        "path": target,
        "add": add,
        "rows": selected.filter(pl.col("symbol").is_in(add)),
        "skipped": skipped,
    }


def apply_day(warehouse: Path, backup_dir: Path, item: dict) -> dict:
    target: Path = item["path"]
    if not item["add"]:
        return {"day": str(item["day"]), "added_rows": 0, "skipped": item["skipped"]}
    old = pl.read_parquet(target) if target.exists() else \
        pl.from_arrow(schema.BAR_MINUTE_1M.empty_table())
    if target.exists():
        backup = backup_dir / target.relative_to(warehouse)
        if backup.exists():
            raise FileExistsError(f"backup already exists: {backup}")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
    rows = item["rows"].select(schema.BAR_MINUTE_1M.names)
    new = pl.concat([old.select(schema.BAR_MINUTE_1M.names), rows]).sort(_KEYS)
    _validate(new, item["day"])
    Warehouse(warehouse).write_day(
        "bar_minute_1m", new.to_arrow().cast(schema.BAR_MINUTE_1M), day=item["day"]
    )
    back = pl.read_parquet(target).select(schema.BAR_MINUTE_1M.names)
    kept = back.filter(~pl.col("symbol").is_in(item["add"])).sort(_KEYS)
    added = back.filter(pl.col("symbol").is_in(item["add"])).sort(_KEYS)
    if not kept.equals(old.select(schema.BAR_MINUTE_1M.names).sort(_KEYS)):
        raise RuntimeError(f"{item['day']} 原有行写后不一致")
    if not added.equals(rows.sort(_KEYS)):
        raise RuntimeError(f"{item['day']} 补入行写后不一致")
    return {
        "day": str(item["day"]),
        "rows_before": old.height,
        "rows_after": back.height,
        "added_rows": rows.height,
        "added_symbols": item["add"],
        "skipped": item["skipped"],
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
        items = [item for day in _days(args.warehouse, args.start, args.end)
                 if (item := plan_day(args.warehouse, day)) is not None]
        summary: dict[str, object] = {
            "operation": "repair_canonical_minute_overrides",
            "applied": args.apply,
            "days": len(items),
            "symbol_days": sum(len(item["add"]) for item in items),
            "rows": sum(item["rows"].height for item in items),
            "skipped": {str(item["day"]): item["skipped"] for item in items if item["skipped"]},
            "changes": [],
        }
        if args.apply:
            summary["changes"] = [apply_day(args.warehouse, args.backup_dir, item)
                                  for item in items]
    text = json.dumps(summary, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
