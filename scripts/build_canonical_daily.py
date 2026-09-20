#!/usr/bin/env python3
"""并行构建阶段 4 规范日线窄表并输出对账；不切换生产消费者。"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from time import monotonic

from qsys.data.pipeline.canonical_daily import (
    build_adjustment_factor_daily,
    build_bar_daily,
    build_share_capital_history,
    build_valuation_daily,
    daily_reconciliation,
    require_canonical_daily_sources,
)
from qsys.data.storage import Warehouse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    warehouse = Warehouse(args.warehouse)
    require_canonical_daily_sources(warehouse, args.start, args.end)

    counts: dict[str, int] = {}
    elapsed: dict[str, float] = {}
    steps = (
        ("bar_daily", lambda: build_bar_daily(warehouse, args.start, args.end)),
        ("valuation_daily", lambda: build_valuation_daily(warehouse, args.start, args.end)),
        ("share_capital_history", lambda: build_share_capital_history(warehouse)),
        ("adjustment_factor_daily", lambda: build_adjustment_factor_daily(
            warehouse, args.start, args.end
        )),
    )
    for name, run in steps:
        started = monotonic()
        counts[name] = run()
        elapsed[name] = round(monotonic() - started, 6)

    diffs = daily_reconciliation(warehouse, args.start, args.end)
    report = {
        "scope": {"start": str(args.start), "end": str(args.end)},
        "counts": counts,
        "elapsed_s": elapsed,
        "reconciliation": {
            name: {
                "matches": result.matches,
                "source_rows": result.left_rows,
                "target_rows": result.right_rows,
                "source_only": len(result.left_only),
                "target_only": len(result.right_only),
                "mismatched": len(result.mismatched),
            }
            for name, result in diffs.items()
        },
        "consumer_switch": "not_performed",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not all(item.matches for item in diffs.values()):
        raise SystemExit("规范日线对账失败；新表保留，生产消费者未切换")


if __name__ == "__main__":
    main()
