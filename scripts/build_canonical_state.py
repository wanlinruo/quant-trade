#!/usr/bin/env python3
"""并行构建阶段 5 规范状态与派生表并输出对账；不切换生产消费者。"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from time import monotonic

from qsys.data.pipeline.canonical_state import (
    build_market_flags_daily,
    build_market_regime_daily,
    build_price_limit_daily,
    build_security_status_daily,
    require_canonical_state_sources,
    state_reconciliation,
)
from qsys.data.storage import Warehouse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    warehouse = Warehouse(args.warehouse)
    require_canonical_state_sources(warehouse, args.start, args.end)

    counts: dict[str, int] = {}
    elapsed: dict[str, float] = {}
    steps = (
        ("price_limit_daily", build_price_limit_daily),
        ("security_status_daily", build_security_status_daily),
        ("market_flags_daily", build_market_flags_daily),
        ("market_regime_daily", build_market_regime_daily),
    )
    for name, build in steps:
        started = monotonic()
        counts[name] = build(warehouse, args.start, args.end)
        elapsed[name] = round(monotonic() - started, 6)

    diffs = state_reconciliation(warehouse, args.start, args.end)
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
    if not all(result.matches for result in diffs.values()):
        raise SystemExit("规范状态/派生表对账失败；生产消费者未切换")


if __name__ == "__main__":
    main()
