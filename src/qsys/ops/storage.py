"""节点存储的保留策略与容量告警。

生产节点 ``/data/qsys`` 下除了 warehouse，还有切源回滚副本、隔离构建仓库、暂存区、
测试快照和 ``~/scratch`` 临时工作区。这里给每一类登记保留策略，盘点时逐项给出：

- **占用**：目录内文件去重后的磁盘块（硬链接只算一次）；
- **可释放**：只被该目录引用的 inode——所有硬链接都在目录内——删除后真正腾出的
  空间。测试快照与 ``*.pre-*`` 副本大量硬链接到 warehouse，占用远大于可释放；
- **判定**：``permanent`` 永久保留、``keep`` 未到期、``protected`` 被当前环境引用、
  ``eligible`` 已到期**可申请删除**。

本模块只读，不删除任何东西：到期项需要维护者逐项批准后再手工处理，批准与删除记录
写进部署记录目录。``/data/qsys`` 顶层出现未登记的条目同样报出，逼着新目录先定策略。
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

GIB = 1 << 30
# 回填等长任务的空间守卫是 10 GiB（canonical_backfill.ManifestStore），低于它直接失败；
# 20 GiB 以下提前告警，留出一次整年分钟回填的余量。
FREE_FAIL_BYTES = 10 * GIB
FREE_WARN_BYTES = 20 * GIB

PERMANENT, DAYS, LATEST, CONTAINER = "permanent", "days", "latest", "container"
KEEP, ELIGIBLE, PROTECTED = "keep", "eligible", "protected"


@dataclass(frozen=True, slots=True)
class RetentionRule:
    root: str        # "base"（/data/qsys）或 "scratch"（~/scratch）
    pattern: str     # 相对 root 的 glob
    category: str
    policy: str      # permanent / days / latest / container
    keep: int = 0    # days：保留天数；latest：保留最新几份
    note: str = ""


RULES: tuple[RetentionRule, ...] = (
    RetentionRule("base", "warehouse", "生产仓库", CONTAINER,
                  note="数据集由指标字典与质量规则管理，不在本策略内"),
    RetentionRule("base", "warehouse/backups/*", "操作回滚副本", DAYS, 30,
                  "切源、修复前的原样副本；变更验证通过 30 天后可申请删除"),
    RetentionRule("base", "warehouse/*.pre-*", "操作回滚副本", DAYS, 30,
                  "整表重建前的原样副本；同上"),
    RetentionRule("base", "backups/*", "操作回滚副本", DAYS, 30, "仓库外的回滚副本；同上"),
    RetentionRule("base", "backups", "操作回滚副本", CONTAINER),
    RetentionRule("base", "baidupan", "付费原始包", PERMANENT,
                  note="付费源原始文件；异地备份建立前是唯一原始副本"),
    RetentionRule("base", "minute-rebuild", "重建暂存区", DAYS, 30,
                  "付费分钟暂存与质量产物，可由原始包重建"),
    RetentionRule("base", "qmt-minute-2026", "隔离构建仓库", DAYS, 30,
                  "QMT 全市场分钟隔离仓库，已发布进生产"),
    RetentionRule("base", "repair-cache", "修复缓存", DAYS, 30, "补取数据的中间缓存"),
    RetentionRule("base", "test-env", "测试环境", CONTAINER),
    RetentionRule("base", "test-env/var", "测试环境运行状态", PERMANENT),
    RetentionRule("base", "test-env/warehouse-current", "测试环境当前快照指针", PERMANENT),
    RetentionRule("base", "test-env/warehouse-snapshots", "测试仓库快照", CONTAINER),
    RetentionRule("base", "test-env/snapshots", "测试数据快照", CONTAINER),
    RetentionRule("base", "test-env/warehouse-snapshots/*", "测试仓库快照", LATEST, 2,
                  "保留最新 2 份；当前指针指向的快照始终保留"),
    RetentionRule("base", "test-env/snapshots/*", "测试数据快照", LATEST, 2, "保留最新 2 份"),
    RetentionRule("base", "var-deployments", "部署与操作记录", PERMANENT),
    RetentionRule("base", "var-prod", "生产运行状态", PERMANENT),
    RetentionRule("base", "lost+found", "文件系统", PERMANENT),
    RetentionRule("base", "storage-migration-*.json", "迁移记录", PERMANENT),
    RetentionRule("scratch", "*", "临时工作区", DAYS, 14, "排查脚本与临时工作树"),
)

# 这些软链接指向的目标被当前环境使用，即使按策略到期也不能删。
PROTECTING_LINKS = ("test-env/warehouse-current",)


@dataclass(frozen=True, slots=True)
class StorageItem:
    path: Path
    category: str
    policy: str
    apparent_bytes: int
    exclusive_bytes: int
    newest: datetime | None
    verdict: str
    reason: str


@dataclass(frozen=True, slots=True)
class DiskStatus:
    total: int
    used: int
    free: int

    @property
    def level(self) -> str:
        if self.free < FREE_FAIL_BYTES:
            return "fail"
        return "warn" if self.free < FREE_WARN_BYTES else "ok"


def disk_status(path: Path) -> DiskStatus:
    usage = shutil.disk_usage(path)
    return DiskStatus(usage.total, usage.used, usage.free)


def _walk(path: Path) -> Iterable[os.stat_result]:
    """目录内每个条目（不跟随软链接）的 lstat；单个文件或软链接也可。"""
    yield path.lstat()
    if path.is_dir() and not path.is_symlink():
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            for name in (*dirnames, *filenames):
                try:
                    yield os.lstat(os.path.join(dirpath, name))
                except FileNotFoundError:
                    continue


def measure(path: Path) -> tuple[int, int, datetime | None]:
    """返回（去重占用字节，独占可释放字节，最新修改时间）。"""
    seen: dict[tuple[int, int], list[int]] = {}
    newest = 0.0
    for stat in _walk(path):
        newest = max(newest, stat.st_mtime)
        key = (stat.st_dev, stat.st_ino)
        if key in seen:
            seen[key][0] += 1
        else:
            seen[key] = [1, stat.st_nlink, stat.st_blocks * 512]
    apparent = sum(blocks for _count, _links, blocks in seen.values())
    exclusive = sum(blocks for count, links, blocks in seen.values() if count >= links)
    return apparent, exclusive, datetime.fromtimestamp(newest) if newest else None


def _protected_targets(base: Path) -> set[Path]:
    targets = set()
    for link in PROTECTING_LINKS:
        candidate = base / link
        if candidate.is_symlink():
            targets.add(candidate.resolve())
    return targets


def inventory(
    base: Path, scratch: Path | None, now: datetime,
    rules: Sequence[RetentionRule] = RULES,
) -> list[StorageItem]:
    roots = {"base": base, "scratch": scratch}
    protected = _protected_targets(base)
    items: list[StorageItem] = []
    for rule in rules:
        root = roots[rule.root]
        if root is None or rule.policy == CONTAINER:
            continue
        matches = sorted(p for p in root.glob(rule.pattern))
        measured = [(p, *measure(p)) for p in matches]
        ranked = sorted(measured, key=lambda m: m[3] or datetime.min, reverse=True)
        latest_kept = {m[0] for m in ranked[: rule.keep]} if rule.policy == LATEST else set()
        for path, apparent, exclusive, newest in measured:
            age = (now - newest).days if newest else None
            if rule.policy == PERMANENT:
                verdict, reason = PERMANENT, rule.note or "永久保留"
            elif path.resolve() in protected:
                verdict, reason = PROTECTED, "被当前环境的软链接引用"
            elif rule.policy == LATEST:
                verdict, reason = (
                    (KEEP, f"最新 {rule.keep} 份之一") if path in latest_kept
                    else (ELIGIBLE, f"超出最新 {rule.keep} 份")
                )
            elif age is not None and age > rule.keep:
                verdict, reason = ELIGIBLE, f"最近修改于 {age} 天前，超过 {rule.keep} 天"
            else:
                verdict, reason = KEEP, f"最近修改于 {age} 天前，未满 {rule.keep} 天"
            items.append(StorageItem(path, rule.category, rule.policy, apparent, exclusive,
                                     newest, verdict, reason))
    return items


def unregistered(base: Path, rules: Sequence[RetentionRule] = RULES) -> list[Path]:
    """``/data/qsys`` 顶层与容器目录下没有任何规则认领的条目。"""
    patterns = [rule.pattern for rule in rules if rule.root == "base"]
    containers = [rule.pattern for rule in rules if rule.root == "base"
                  and rule.policy == CONTAINER and rule.pattern != "warehouse"]
    candidates = [*base.iterdir()]
    for container in containers:
        folder = base / container
        if folder.is_dir():
            candidates.extend(folder.iterdir())
    claimed = {p for pattern in patterns for p in base.glob(pattern)}
    return sorted(p for p in candidates if p not in claimed)
