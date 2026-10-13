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
import os
from datetime import datetime
from pathlib import Path

from qsys.ops.storage import (
    ELIGIBLE,
    FREE_FAIL_BYTES,
    FREE_WARN_BYTES,
    GIB,
    disk_status,
    inventory,
    unregistered,
)

# 剩余空间低于此值时启动扩盘评估。20 GiB 的既有 warning 仍更早提醒，
# 10 GiB 的 fail 仍与回填任务空间守卫保持一致。
EXPANSION_REVIEW_BYTES = 15 * GIB

# 顺序就是共享 inode 的归属优先级：业务仓库优先于测试快照和隔离构建目录。
# 因此同一份硬链接数据只计一次，并归到最接近生产真值的类别。
CATEGORY_ROOTS: tuple[tuple[str, str], ...] = (
    ("warehouse", "生产仓库"),
    ("var-deployments", "部署与操作记录"),
    ("research", "研究产物"),
    ("minute-rebuild", "重建暂存区"),
    ("qmt-minute-2026", "隔离构建仓库"),
    ("test-env", "测试环境"),
    ("backups", "操作回滚副本"),
    ("baidupan", "付费源下载区"),
    ("var-prod", "生产运行状态"),
    ("repair-cache", "修复缓存"),
    ("lost+found", "文件系统"),
)


def _gib(value: int) -> str:
    return f"{value / GIB:7.2f}"


def _tree_stats(path: Path):
    """逐项 lstat，不跟随软链接；消失中的临时文件直接跳过。"""
    try:
        yield path.lstat()
    except FileNotFoundError:
        return
    if not path.is_dir() or path.is_symlink():
        return
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in (*dirnames, *filenames):
            try:
                yield os.lstat(os.path.join(dirpath, name))
            except FileNotFoundError:
                continue


def classified_usage(base: Path) -> list[dict[str, object]]:
    """分类 ``base`` 所在数据盘；跨目录硬链接在全局只计一次。"""
    roots: list[tuple[Path, str]] = []
    claimed: set[Path] = set()
    for relative, category in CATEGORY_ROOTS:
        path = base / relative
        if path.exists() or path.is_symlink():
            roots.append((path, category))
            claimed.add(path)
    for path in sorted(base.iterdir()):
        if path not in claimed:
            roots.append((path, "其他"))
    seen: set[tuple[int, int]] = set()
    totals: dict[str, list[int]] = {}
    for root, category in roots:
        values = totals.setdefault(category, [0, 0])  # bytes, unique inodes
        for stat in _tree_stats(root):
            key = (stat.st_dev, stat.st_ino)
            if key in seen:
                continue
            seen.add(key)
            values[0] += stat.st_blocks * 512
            values[1] += 1
    return [
        {"category": category, "bytes": values[0], "unique_inodes": values[1]}
        for category, values in sorted(totals.items())
    ]


def build_payload(base: Path, scratch: Path | None, now: datetime) -> dict[str, object]:
    items = inventory(base, scratch, now)
    disk = disk_status(base)
    return {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "base": str(base),
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "level": disk.level,
            "expansion_review_required": disk.free < EXPANSION_REVIEW_BYTES,
        },
        "thresholds": {
            "warning_free_bytes": FREE_WARN_BYTES,
            "expansion_review_free_bytes": EXPANSION_REVIEW_BYTES,
            "failure_free_bytes": FREE_FAIL_BYTES,
        },
        # 分类合计只覆盖 disk 所描述的 /data/qsys；scratch 可能在系统盘，
        # 仍参与保留策略 items，但不能混进数据盘容量趋势。
        "categories": classified_usage(base),
        "items": [{
            "path": str(item.path), "category": item.category, "policy": item.policy,
            "apparent_bytes": item.apparent_bytes, "exclusive_bytes": item.exclusive_bytes,
            "newest": item.newest.isoformat(timespec="seconds") if item.newest else None,
            "verdict": item.verdict, "reason": item.reason,
        } for item in items],
        "unregistered": [str(path) for path in unregistered(base)],
    }


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def history_path(root: Path, now: datetime) -> Path:
    return root / f"{now:%Y/%m/%d}" / f"storage-{now:%H%M%S}-{os.getpid()}.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", type=Path, default=Path("/data/qsys"))
    parser.add_argument("--scratch", type=Path, default=Path.home() / "scratch")
    parser.add_argument("--json", type=Path, help="同时写出 JSON 报告")
    parser.add_argument("--history-root", type=Path,
                        help="按日期原子写入历史快照，用于容量趋势")
    args = parser.parse_args()

    now = datetime.now()
    scratch = args.scratch if args.scratch.exists() else None
    payload = build_payload(args.base, scratch, now)
    items = payload["items"]
    stray = payload["unregistered"]
    disk = payload["disk"]

    print(f"盘点时间 {now:%F %T}；磁盘 共 {disk['total'] / GIB:.1f} GiB，"
          f"已用 {disk['used'] / GIB:.1f} GiB，剩余 {disk['free'] / GIB:.1f} GiB"
          f"（{disk['level']}）")
    print(f"{'判定':<10}{'类别':<12}{'占用GiB':>8}{'可释放GiB':>10}  {'最近修改':<11} 路径 / 说明")
    for item in sorted(items, key=lambda i: (
        i["verdict"] != ELIGIBLE, i["category"], i["path"],
    )):
        newest = item["newest"][:10] if item["newest"] else "-"
        print(f"{item['verdict']:<10}{item['category']:<12}{_gib(item['apparent_bytes']):>8}"
              f"{_gib(item['exclusive_bytes']):>10}  {newest:<11} {item['path']}"
              f"  （{item['reason']}）")
    eligible = [item for item in items if item["verdict"] == ELIGIBLE]
    print(f"到期 {len(eligible)} 项，合计可释放 "
          f"{sum(item['exclusive_bytes'] for item in eligible) / GIB:.2f} GiB")
    for path in stray:
        print(f"未登记保留策略：{path}")

    if args.json:
        write_json_atomic(args.json, payload)
    if args.history_root:
        target = history_path(args.history_root, now)
        write_json_atomic(target, payload)
        print(f"历史快照：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
