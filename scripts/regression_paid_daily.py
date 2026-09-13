"""换源回归对账的命令行入口。逻辑在 qsys.data.quality.source_diff。

用法:
    uv run python scripts/regression_paid_daily.py \
        --warehouse /data/qsys/warehouse --start 2023-01-03 --end 2026-09-09 \
        --out artifacts/regression_stage1.json
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from qsys.data.quality.source_diff import compare_daily_sources
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--boards", default="main,gem,star",
                        help="逗号分隔；传空串表示不过滤")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    boards = {b.strip() for b in args.boards.split(",") if b.strip()} or None
    query = WarehouseQuery(Warehouse(args.warehouse))
    diff = compare_daily_sources(query, args.start, args.end, boards)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(diff.summary, indent=2, ensure_ascii=False))
    for name, frame in (("only_in_paid", diff.only_in_paid),
                        ("only_in_legacy", diff.only_in_legacy),
                        ("value_diffs", diff.value_diffs)):
        frame.write_parquet(args.out.with_name(f"{args.out.stem}_{name}.parquet"))

    print(json.dumps(diff.summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
