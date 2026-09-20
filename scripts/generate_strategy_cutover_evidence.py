#!/usr/bin/env python3
"""生成 audit_cutover.py 可直接读取的策略输入对账证据。"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path

from qsys.data.cutover_evidence import strategy_input_comparison
from qsys.data.storage import Warehouse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = strategy_input_comparison(
        Warehouse(args.warehouse), args.start, args.end
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    if not result.matches or result.compared_cases <= 0:
        raise SystemExit("策略输入对账失败或没有可比较输入")


if __name__ == "__main__":
    main()
