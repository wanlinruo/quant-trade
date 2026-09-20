#!/usr/bin/env python3
"""生成阶段 11 切换门禁报告；只读仓库，不执行切换或清理。"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import subprocess

from qsys.data.cutover import (
    BenchmarkComparison,
    StrategyComparison,
    audit_cutover,
)
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def _git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _load_performance(path: Path | None):
    if path is None:
        return None
    rows = json.loads(path.read_text(encoding="utf-8"))
    return tuple(BenchmarkComparison(**row) for row in rows)


def _load_strategy(path: Path | None):
    if path is None:
        return None
    return StrategyComparison(**json.loads(path.read_text(encoding="utf-8")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--performance", type=Path)
    parser.add_argument("--strategy", type=Path)
    parser.add_argument("--min-cycle-sessions", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    commit = _git(["rev-parse", "HEAD"])
    clean = not _git(["status", "--porcelain"])
    report = audit_cutover(
        WarehouseQuery(Warehouse(args.warehouse)), args.start, args.end,
        commit_sha=commit, release_clean=clean,
        performance=_load_performance(args.performance),
        strategy=_load_strategy(args.strategy),
        min_cycle_sessions=args.min_cycle_sessions,
    )
    payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    if not report.ready:
        raise SystemExit("切换门禁未通过；未执行消费者切换、冻结或清理")


if __name__ == "__main__":
    main()
