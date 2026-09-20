#!/usr/bin/env python3
"""生成 audit_cutover.py 可读取的查询性能证据。"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime
import json
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time

from qsys.data.cutover import (
    BenchmarkComparison,
    CutoverConfig,
    CutoverMarketData,
    FacadeMode,
)
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def _peak_mb() -> float:
    raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return raw / (1024 * 1024) if sys.platform == "darwin" else raw / 1024


def _canonical_minute_files(root: Path, start: date, end: date) -> int:
    count = 0
    base = root / "bar_minute_1m"
    for year in range(start.year, end.year + 1):
        for path in (base / f"year={year}").glob("date=*/part.parquet"):
            day = datetime.strptime(path.parent.name.split("=", 1)[1], "%Y%m%d").date()
            count += int(start <= day <= end)
    return count


def _worker(args) -> dict:
    warehouse = Warehouse(args.warehouse)
    query = WarehouseQuery(warehouse)
    mode = FacadeMode(args.worker_mode)
    facade = CutoverMarketData(query, CutoverConfig(mode))
    lo = datetime.combine(args.start, datetime.min.time())
    hi = datetime.combine(args.end, datetime.max.time())

    def run():
        if args.worker_kind == "daily":
            return facade.daily_bars(None, args.start, args.end)
        return facade.minute_bars(None, lo, hi)

    run()  # 单独预热文件元数据和 DuckDB 连接
    elapsed = []
    rows = 0
    for _ in range(args.repeats):
        started = time.perf_counter()
        frame = run()
        elapsed.append((time.perf_counter() - started) * 1000)
        rows = frame.height
    if args.worker_kind == "daily":
        dataset = "tushare_daily" if mode is FacadeMode.LEGACY else "bar_daily"
        files = sum(
            (warehouse.root / dataset / f"year={year}" / "part.parquet").is_file()
            for year in range(args.start.year, args.end.year + 1)
        )
    elif mode is FacadeMode.LEGACY:
        files = len(query._minute_files(args.start, args.end))
    else:
        files = _canonical_minute_files(warehouse.root, args.start, args.end)
    return {
        "median_ms": statistics.median(elapsed), "peak_mb": _peak_mb(),
        "scanned_files": files, "scanned_columns": 8, "rows": rows,
    }


def _measure(args, kind: str, mode: str) -> dict:
    command = [
        sys.executable, str(Path(__file__).resolve()),
        "--warehouse", str(args.warehouse), "--start", args.start.isoformat(),
        "--end", args.end.isoformat(), "--repeats", str(args.repeats),
        "--worker-kind", kind, "--worker-mode", mode,
    ]
    return json.loads(subprocess.run(
        command, check=True, capture_output=True, text=True
    ).stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-regression-ratio", type=float, default=1.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-kind", choices=("daily", "minute"), help=argparse.SUPPRESS)
    parser.add_argument("--worker-mode", choices=("legacy", "canonical"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats 必须大于 0")
    if args.worker_kind or args.worker_mode:
        if not args.worker_kind or not args.worker_mode:
            parser.error("worker 参数必须成对出现")
        print(json.dumps(_worker(args)))
        return

    comparisons = []
    for kind in ("daily", "minute"):
        legacy = _measure(args, kind, "legacy")
        canonical = _measure(args, kind, "canonical")
        comparisons.append(BenchmarkComparison(
            kind, legacy["median_ms"], canonical["median_ms"],
            legacy["scanned_files"], canonical["scanned_files"],
            legacy["scanned_columns"], canonical["scanned_columns"],
            legacy["peak_mb"], canonical["peak_mb"], args.max_regression_ratio,
        ))
    payload = json.dumps(
        [asdict(item) for item in comparisons], ensure_ascii=False, indent=2
    ) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    if not all(item.passed for item in comparisons):
        raise SystemExit("规范查询性能低于约定基线")


if __name__ == "__main__":
    main()
