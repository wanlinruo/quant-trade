#!/usr/bin/env python3
"""清除 minute_bars 中 QMT 区间查询产生的昨收价填充行。

规则：每个（标的，交易日）保留首笔成交（volume>0）及其之后的行；该日无任何成交则
整日丢弃。纯删除，不新增、不推算任何值。
详见 docs/reference/qmt-range-query-padding.md。

逐日重写分区，先出报告后落盘——不加 `--apply` 不写任何文件。
"""

import argparse
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import polars as pl

from qsys.data import schema
from qsys.data.storage import Warehouse


def drop_padding(frame: pl.DataFrame) -> pl.DataFrame:
    """丢弃每个（标的，日）首笔成交之前的行；整日无成交则整日丢弃。"""
    if frame.height == 0:
        return frame
    framed = frame.with_columns(pl.col("dt").dt.date().alias("_d"))
    first = (framed.filter(pl.col("volume") > 0)
                   .group_by(["symbol", "_d"])
                   .agg(pl.col("dt").min().alias("_first")))
    return (framed.join(first, on=["symbol", "_d"], how="inner")
                  .filter(pl.col("dt") >= pl.col("_first"))
                  .drop(["_d", "_first"])
                  .sort(["symbol", "dt"]))


def iter_partitions(root: Path) -> Iterator[tuple[Path, date | None, int]]:
    """产出 (parquet 路径, 该分区的交易日或 None, 年份)。

    `minute_bars` **同时存在两种落盘布局**：

    - `year=YYYY/date=YYYYMMDD/part.parquet` —— 按日分区，用 `write_day` 回写
    - `year=YYYY/part.parquet`               —— 整年一个文件，用 `write(year=)` 回写

    只认按日那种会**静默**跳过整年文件：2026-10-04 的只读报告就漏掉了 2025 全年
    2,874,648 行，而报告本身给出的是一个看着合理的数字。
    """
    for year_dir in sorted(root.glob("year=*")):
        year = int(year_dir.name.removeprefix("year="))
        whole = year_dir / "part.parquet"
        if whole.exists():
            yield whole, None, year
        for day_dir in sorted(year_dir.glob("date=*")):
            part = day_dir / "part.parquet"
            if part.exists():
                stamp = day_dir.name.removeprefix("date=")
                yield part, date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8])), year


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--warehouse", type=Path, required=True)
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    ap.add_argument("--apply", action="store_true", help="不加则只报告，不落盘")
    args = ap.parse_args()

    warehouse = Warehouse(args.warehouse)
    total_before = total_after = 0
    touched = []
    in_range = pl.col("dt").dt.date().is_between(args.start, args.end)

    for path, day, year in iter_partitions(args.warehouse / "minute_bars"):
        if day is not None and not (args.start <= day <= args.end):
            continue
        frame = pl.read_parquet(path)
        # 只清区间内的行，区间外的原样保留——整年文件会横跨区间边界。
        keep = frame.filter(~in_range)
        cleaned = pl.concat([keep, drop_padding(frame.filter(in_range))]).sort(["symbol", "dt"])
        total_before += frame.height
        total_after += cleaned.height
        if cleaned.height == frame.height:
            continue
        label = day.isoformat() if day is not None else f"year={year}（整年文件）"
        touched.append((label, frame.height, cleaned.height))
        if args.apply:
            table = cleaned.to_arrow().cast(schema.MINUTE_BARS)
            if day is not None:
                warehouse.write_day("minute_bars", table, day=day)
            else:
                warehouse.write("minute_bars", table, year=year)

    print(f"扫描 {total_before:,} 行 -> {total_after:,} 行，"
          f"删除 {total_before - total_after:,} 行，涉及 {len(touched)} 个分区")
    for label, before, after in touched[:20]:
        print(f"  {label}  {before:,} -> {after:,}  (-{before - after:,})")
    if len(touched) > 20:
        print(f"  …… 另有 {len(touched) - 20} 个分区")
    if not args.apply:
        print("（未加 --apply，没有写入任何文件）")


if __name__ == "__main__":
    main()
