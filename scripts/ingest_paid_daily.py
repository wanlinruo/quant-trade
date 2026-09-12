#!/usr/bin/env python3
"""在 Linux 上随下载进度按年落库；--wait 在缺文件时持续等待。"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import time

from qsys.data.pipeline.paid_daily import atomic_json, audit, import_year, inventory_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incoming", type=Path, default=Path("var/baidupan/按交易日"))
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--inventory", type=Path, default=Path("var/baidupan/inventory-20260912.json"))
    parser.add_argument("--remote-root", default="/金融数据/每日日线/20260911/按交易日")
    parser.add_argument("--base-date", type=date.fromisoformat, required=True)
    parser.add_argument("--year", type=int, action="append", dest="selected_years",
                        help="只重建指定年份，可重复；用于历史源文件清理后的日常增量")
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    entries = inventory_files(args.inventory, args.remote_root)
    available_years = sorted({int(Path(e["path"]).stem[:4]) for e in entries})
    if args.selected_years:
        unknown = set(args.selected_years) - set(available_years)
        if unknown:
            parser.error(f"inventory does not contain years: {sorted(unknown)}")
        years = sorted(set(args.selected_years))
    else:
        years = available_years
    completed = {}
    while len(completed) < len(years):
        for year in years:
            if year in completed:
                continue
            result = import_year(args.incoming, args.warehouse, entries, args.remote_root, year)
            if result["ready"]:
                completed[year] = {k: v for k, v in result.items() if k != "source_files"}
                print(f"year={year} files={result['files']} rows={result['rows']} ready", flush=True)
        atomic_json(args.incoming.parent / "ingest-status.json", {
            "expected_files": len(entries), "completed_years": completed,
            "pending_years": [y for y in years if y not in completed],
        })
        if len(completed) < len(years):
            if not args.wait:
                raise SystemExit("部分年份文件未到齐；重跑或用 --wait 等待下载")
            time.sleep(30)
    report = audit(args.warehouse, args.base_date, len(entries))
    print(report, flush=True)


if __name__ == "__main__":
    main()
