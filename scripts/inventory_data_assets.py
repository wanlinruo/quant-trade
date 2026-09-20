#!/usr/bin/env python3
"""生成只读数据资产盘点 JSON。"""

from __future__ import annotations

import argparse
from pathlib import Path

from qsys.data.catalog.inventory import build_inventory, write_inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("."))
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--test-result",
        help="已独立执行的测试结论，例如 '1842 passed, 2 warnings in 53.07s'",
    )
    args = parser.parse_args()
    write_inventory(
        build_inventory(args.project, args.warehouse, test_result=args.test_result),
        args.output,
    )
    print(args.output)


if __name__ == "__main__":
    main()
