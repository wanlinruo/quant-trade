#!/usr/bin/env python3
"""逐年流式暂存、质量验收并发布百度网盘付费 1 分钟数据。"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from qsys.data.pipeline.paid_minute import (
    finalize_day_staging,
    ingest_day_file,
    ingest_year,
    publish_year,
    quarantine_unmatched_zero_trade_days,
    validate_year,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage", help="从年度 ZIP 流式写入暂存分片")
    stage.add_argument("--zip", type=Path, required=True)
    stage.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    stage.add_argument("--year", type=int, required=True)
    stage.add_argument("--shard-members", type=int, default=64)
    days = sub.add_parser("stage-days", help="从按交易日的全市场 CSV 目录写入暂存（D-20，2026 起）")
    days.add_argument("--dir", type=Path, required=True)
    days.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    days.add_argument("--year", type=int, required=True)
    publish = sub.add_parser("publish", help="质量验收后原子发布年度目录")
    publish.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    publish.add_argument("--year", type=int, required=True)
    validate = sub.add_parser("validate", help="生成日线与 QMT 交叉质量报告")
    validate.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    validate.add_argument("--year", type=int, required=True)
    validate.add_argument("--daily-dataset", default="paid_daily",
                          help="对照日线；2026 起付费日线已冻结，用 tushare_daily")
    quarantine = sub.add_parser("quarantine-placeholders", help="隔离无日线记录的零成交恒价占位日")
    quarantine.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    quarantine.add_argument("--year", type=int, required=True)
    args = parser.parse_args()

    if args.command == "stage":
        result = ingest_year(args.zip, args.warehouse, args.year, shard_members=args.shard_members)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    elif args.command == "stage-days":
        import re
        from datetime import datetime as _dt

        files = sorted(args.dir.glob("*.csv"))
        for n, path in enumerate(files, 1):
            match = re.match(r"^(\d{8})_", path.name)
            if match is None:
                raise SystemExit(f"无法从文件名识别交易日: {path.name}")
            day = _dt.strptime(match.group(1), "%Y%m%d").date()
            if day.year != args.year:
                continue
            record = ingest_day_file(path, args.warehouse, day)
            print(f"[{n}/{len(files)}] {day} rows={record['rows']:,} symbols={record['symbols']}", flush=True)
        print(json.dumps({k: v for k, v in finalize_day_staging(args.warehouse, args.year).items()
                          if k != "day_inventory"}, ensure_ascii=False, indent=2))
    elif args.command == "publish":
        print(publish_year(args.warehouse, args.year))
    elif args.command == "validate":
        print(json.dumps(validate_year(args.warehouse, args.year, daily_dataset=args.daily_dataset),
                         ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(
            quarantine_unmatched_zero_trade_days(args.warehouse, args.year),
            ensure_ascii=False, indent=2, default=str,
        ))


if __name__ == "__main__":
    main()
