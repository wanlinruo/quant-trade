#!/usr/bin/env python3
"""并行构建阶段 6 规范一分钟线；不切换生产消费者。"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from qsys.data.pipeline.canonical_minute import build_bar_minute_1m
from qsys.data.storage import Warehouse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    result = build_bar_minute_1m(Warehouse(args.warehouse), args.start, args.end)
    print(json.dumps({
        "scope": {"start": str(args.start), "end": str(args.end)},
        "days": result.days,
        "rows": result.rows,
        "symbol_days": result.symbol_days,
        "nonstandard_counts": result.nonstandard_counts,
        "sources": result.sources,
        "consumer_switch": "not_performed",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
