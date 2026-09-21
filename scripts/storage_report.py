#!/usr/bin/env python3
"""节点存储盘点：按保留策略列出备份、暂存与临时目录的占用、可释放空间与判定。

只读，不删除任何东西。「可释放」只计只被该目录引用的 inode，硬链接到 warehouse 的
文件不算。到期项需维护者逐项批准后手工删除，并在 var-deployments 留记录。

用法：
  uv run python scripts/storage_report.py                     # 默认 /data/qsys 与 ~/scratch
  uv run python scripts/storage_report.py --json out.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from qsys.ops.storage import ELIGIBLE, GIB, disk_status, inventory, unregistered


def _gib(value: int) -> str:
    return f"{value / GIB:7.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", type=Path, default=Path("/data/qsys"))
    parser.add_argument("--scratch", type=Path, default=Path.home() / "scratch")
    parser.add_argument("--json", type=Path, help="同时写出 JSON 报告")
    args = parser.parse_args()

    now = datetime.now()
    scratch = args.scratch if args.scratch.exists() else None
    items = inventory(args.base, scratch, now)
    stray = unregistered(args.base)
    disk = disk_status(args.base)

    print(f"盘点时间 {now:%F %T}；磁盘 共 {disk.total / GIB:.1f} GiB，"
          f"已用 {disk.used / GIB:.1f} GiB，剩余 {disk.free / GIB:.1f} GiB（{disk.level}）")
    print(f"{'判定':<10}{'类别':<12}{'占用GiB':>8}{'可释放GiB':>10}  {'最近修改':<11} 路径 / 说明")
    for item in sorted(items, key=lambda i: (i.verdict != ELIGIBLE, i.category, str(i.path))):
        newest = f"{item.newest:%F}" if item.newest else "-"
        print(f"{item.verdict:<10}{item.category:<12}{_gib(item.apparent_bytes):>8}"
              f"{_gib(item.exclusive_bytes):>10}  {newest:<11} {item.path}  （{item.reason}）")
    eligible = [item for item in items if item.verdict == ELIGIBLE]
    print(f"到期 {len(eligible)} 项，合计可释放 "
          f"{sum(item.exclusive_bytes for item in eligible) / GIB:.2f} GiB")
    for path in stray:
        print(f"未登记保留策略：{path}")

    if args.json:
        payload = {
            "generated_at": now.isoformat(timespec="seconds"),
            "disk": {"total": disk.total, "used": disk.used, "free": disk.free,
                     "level": disk.level},
            "items": [{
                "path": str(item.path), "category": item.category, "policy": item.policy,
                "apparent_bytes": item.apparent_bytes, "exclusive_bytes": item.exclusive_bytes,
                "newest": item.newest.isoformat(timespec="seconds") if item.newest else None,
                "verdict": item.verdict, "reason": item.reason,
            } for item in items],
            "unregistered": [str(path) for path in stray],
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
