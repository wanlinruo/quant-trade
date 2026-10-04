#!/usr/bin/env python3
"""清除 minute_bars 中 QMT 区间查询产生的昨收价填充行。

规则：每个（标的，交易日）保留首笔成交（volume>0）及其之后的行；该日无任何成交则
整日丢弃。纯删除，不新增、不推算任何值。
详见 docs/reference/qmt-range-query-padding.md。

逐日重写分区，先出报告后落盘——不加 `--apply` 不写任何文件。
"""

import argparse
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
    for path in sorted((args.warehouse / "minute_bars").glob("year=*/date=*/part.parquet")):
        stamp = path.parent.name.removeprefix("date=")
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
        if not (args.start <= day <= args.end):
            continue
        frame = pl.read_parquet(path)
        cleaned = drop_padding(frame)
        total_before += frame.height
        total_after += cleaned.height
        if cleaned.height == frame.height:
            continue
        touched.append((day, frame.height, cleaned.height))
        if args.apply:
            warehouse.write_day("minute_bars", cleaned.to_arrow().cast(schema.MINUTE_BARS),
                                day=day)

    print(f"扫描 {total_before:,} 行 -> {total_after:,} 行，"
          f"删除 {total_before - total_after:,} 行，涉及 {len(touched)} 个交易日")
    for day, before, after in touched[:20]:
        print(f"  {day}  {before:,} -> {after:,}  (-{before - after:,})")
    if len(touched) > 20:
        print(f"  …… 另有 {len(touched) - 20} 个交易日")
    if not args.apply:
        print("（未加 --apply，没有写入任何文件）")


if __name__ == "__main__":
    main()
