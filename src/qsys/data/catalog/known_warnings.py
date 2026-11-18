"""已知警告受管清单与判定（#662 计划 04；设计文档 §7.2）。

清单：检查稳定 ID（check_id，可细到 metric_id）→ Issue + 基线（登记时的违规数与样本指纹）。
登记与调整基线都走代码评审，页面不提供修改入口；条目由 ``scripts/known_warnings_baseline.py``
从真实质检结果生成候选，不手编基线。

判定（逐字取自 §7.2）：
- 违规数不超过基线且没有新样本 → 已知警告；
- 违规数超过基线或出现新样本 → 新警告，给出“比基线多 N”；
- 数据层 ``known_issue == "上游数据自相矛盾"``（#653）→ 上游问题；
- 阻断失败始终为失败，不参与已知判定（#482：真实失败不降级）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl

UPSTREAM_SELF_CONTRADICTION = "上游数据自相矛盾"
UPSTREAM_ISSUE = "#653"
BLOCKING = "blocking"


@dataclass(frozen=True, slots=True)
class KnownWarning:
    check_id: str
    metric_id: str | None  # None：该检查的全部指标
    issue: str
    baseline_violations: int
    baseline_sample_sha256: str | None
    registered_on: date
    note: str


# 受管清单。初始为空：实际条目须由工具从生产质检结果生成，经评审后提交（不编造基线）。
KNOWN_WARNINGS: tuple[KnownWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class Verdict:
    kind: Literal["failed", "new", "known", "upstream", "passed"]
    issue: str | None
    over_baseline: int | None
    reason: str


def lookup(check_id: str, metric_id: str | None,
           entries: Sequence[KnownWarning] = KNOWN_WARNINGS) -> KnownWarning | None:
    exact = next((e for e in entries if e.check_id == check_id and e.metric_id == metric_id), None)
    return exact or next((e for e in entries if e.check_id == check_id and e.metric_id is None), None)


def sample_fingerprint(path: Path | None) -> str | None:
    """违规样本的规范化指纹：列名排序、行按全部列的字符串值排序后取 SHA-256。"""
    if path is None or not Path(path).is_file():
        return None
    frame = pl.read_parquet(path)
    columns = sorted(frame.columns)
    rows = sorted(json.dumps([None if v is None else str(v) for v in r], ensure_ascii=False)
                  for r in frame.select(columns).iter_rows())
    payload = json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def judge(row: Mapping, fingerprint: str | None, *,
          entries: Sequence[KnownWarning] = KNOWN_WARNINGS) -> Verdict:
    """对一行 metric_checks 结果给出判定。fingerprint 为本次违规样本的指纹（没有样本为 None）。"""
    if row.get("passed"):
        return Verdict("passed", None, None, "通过")
    if row.get("severity") == BLOCKING:
        return Verdict("failed", None, None, "阻断失败")
    if (row.get("known_issue") or "").strip() == UPSTREAM_SELF_CONTRADICTION:
        return Verdict("upstream", UPSTREAM_ISSUE, None, UPSTREAM_SELF_CONTRADICTION)
    known = lookup(str(row.get("check_id")), row.get("metric_id"), entries)
    if known is None:
        return Verdict("new", None, None, "未登记的警告")
    if row.get("violations") is None:  # 缺失不当 0（数据铁律）
        return Verdict("new", known.issue, None, "违规数缺失，无法与基线比较")
    over = int(row["violations"]) - known.baseline_violations
    if over > 0:
        return Verdict("new", known.issue, over, f"比基线多 {over}")
    if known.baseline_sample_sha256 is not None:
        if fingerprint is None:
            return Verdict("new", known.issue, 0, "本次没有样本文件，无法证明样本未变")
        if fingerprint != known.baseline_sample_sha256:
            return Verdict("new", known.issue, 0, "出现新样本（样本指纹与基线不同）")
    return Verdict("known", known.issue, 0, "不超过基线且样本未变")
