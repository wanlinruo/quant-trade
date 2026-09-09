"""导出待下载的分钟线清单，供 Windows 侧的下载脚本消费。

用法：
  uv run python scripts/export_minute_manifest.py \
      --start 2023-01-01 --end 2026-09-09 --out var/minute_manifest.csv
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from qsys.data.pipeline.candidates import candidate_stock_days, write_manifest
from qsys.data.storage import Warehouse


def main() -> int:
    parser = argparse.ArgumentParser(description="导出分钟线下载清单")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--out", default="var/minute_manifest.csv")
    parser.add_argument(
        "--window",
        type=int,
        default=1,
        help="首板后取几个交易日；策略移植后如需更长可调大",
    )
    args = parser.parse_args()

    frame = candidate_stock_days(
        Warehouse(Path(args.warehouse)),
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        window=args.window,
    )
    rows = write_manifest(frame, Path(args.out))
    print(f"候选股-日 {rows:,} 组  ≈ {rows * 240 / 1e6:.1f}M 根分钟线")
    print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
