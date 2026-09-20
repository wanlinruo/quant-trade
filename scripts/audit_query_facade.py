#!/usr/bin/env python3
"""对比旧生产查询与阶段 8 新历史门面；只读，不执行消费者切换。"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from qsys.data.access import facade_reconciliation
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    results = facade_reconciliation(
        WarehouseQuery(Warehouse(args.warehouse)), args.start, args.end
    )
    report = {
        name: {
            "matches": result.matches,
            "legacy_rows": result.left_rows,
            "canonical_rows": result.right_rows,
            "legacy_only": len(result.left_only),
            "canonical_only": len(result.right_only),
            "mismatched": len(result.mismatched),
        }
        for name, result in results.items()
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not all(result.matches for result in results.values()):
        raise SystemExit("新旧查询门面对账失败；消费者未切换")


if __name__ == "__main__":
    main()
