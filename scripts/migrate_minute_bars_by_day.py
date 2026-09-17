#!/usr/bin/env python3
"""把 QMT `minute_bars` 的整年文件拆成按交易日分区（D-20）。

逐日回读核对通过后，原整年文件移入备份目录，不删除。可重复执行：已拆分的年份
不存在整年文件，直接跳过。

用法：
  .venv/bin/python scripts/migrate_minute_bars_by_day.py \
      --warehouse /home/ubuntu/qsys/warehouse \
      --backup-dir /home/ubuntu/qsys/warehouse/backups/minute_bars_year_files_<日期>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qsys.data.pipeline.minutes import split_minute_year
from qsys.data.storage import Warehouse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args()

    warehouse = Warehouse(args.warehouse)
    years = sorted(int(p.parent.name.split("=", 1)[1])
                   for p in (args.warehouse / "minute_bars").glob("year=*/part.parquet"))
    if not years:
        print("没有整年文件，无需迁移")
        return 0
    for year in years:
        print(json.dumps(split_minute_year(warehouse, year, backup_dir=args.backup_dir),
                         ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
