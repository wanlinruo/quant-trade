"""数据组页面的纯派生（#662，设计文档 §8.13）。

- quality_runs：按 run_id 汇总近 30 次质检结构；某 run 没有表级记录时 tables 为 None，不当 0。
- warning_sources：最新一次 run 的未通过、非阻断记录按 known_issue 归类（设计者决定）。
- lineage_graph：数据源 → 各层数据集 → 消费者；节点状态由调用方合并。
- table_sample：只读最新分区最后一个行组的一批，不整表加载、不经 DuckDB，null 原样保留。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow.parquet as pq

from qsys.data.catalog import DATASETS, DEPENDENCIES, SOURCES
from qsys.data.catalog.types import Dataset

RUN_LIMIT = 30
SAMPLE_LIMIT = 20
SAMPLES_PER_SOURCE = 5
BLOCKING = "blocking"
UPSTREAM_SELF_CONTRADICTION = "上游数据自相矛盾"
TABLE_STATUSES = ("red", "yellow", "green", "gray")


def quality_runs(metric_checks: pl.DataFrame, dataset_checks: pl.DataFrame,
                 limit: int = RUN_LIMIT) -> list[dict]:
    if metric_checks.height == 0:
        return []
    runs = (metric_checks.group_by("run_id")
            .agg(pl.col("checked_at").max().alias("checked_at"),
                 pl.len().alias("total"),
                 pl.col("passed").fill_null(False).sum().alias("passed"),
                 ((~pl.col("passed").fill_null(False)) & (pl.col("severity") == BLOCKING)).sum().alias("blocking"),
                 ((~pl.col("passed").fill_null(False)) & (pl.col("severity") != BLOCKING)).sum().alias("warning"))
            .sort("checked_at", descending=True).head(limit))
    tables: dict[str, dict[str, int]] = {}
    if dataset_checks.height:
        ids = runs["run_id"].to_list()
        counts = (dataset_checks.filter(pl.col("run_id").is_in(ids))
                  .group_by(["run_id", "status"]).len())
        for run_id, status, n in counts.iter_rows():
            bucket = tables.setdefault(run_id, dict.fromkeys(TABLE_STATUSES, 0))
            if status in bucket:
                bucket[status] = int(n)
    return [{
        "run_id": r["run_id"], "checked_at": r["checked_at"],
        "fields": {"total": int(r["total"]), "passed": int(r["passed"]),
                   "blocking": int(r["blocking"]), "warning": int(r["warning"])},
        "tables": tables.get(r["run_id"]),
    } for r in runs.iter_rows(named=True)]


def warning_sources(metric_checks: pl.DataFrame) -> list[dict]:
    groups = [
        {"key": "upstream", "label": "上游自相矛盾", "issue": "#653", "count": 0, "samples": []},
        {"key": "registered", "label": "已登记缺口", "issue": "#594", "count": 0, "samples": []},
        {"key": "unclassified", "label": "待归类", "issue": None, "count": 0, "samples": []},
    ]
    if metric_checks.height == 0:
        return groups
    latest = (metric_checks.group_by("run_id").agg(pl.col("checked_at").max())
              .sort("checked_at", descending=True)["run_id"][0])
    rows = metric_checks.filter(
        (pl.col("run_id") == latest) & ~pl.col("passed").fill_null(False)
        & (pl.col("severity") != BLOCKING)).sort(["metric_id", "check_id"])
    by_key = {g["key"]: g for g in groups}
    for r in rows.iter_rows(named=True):
        known = (r["known_issue"] or "").strip()
        key = "upstream" if known == UPSTREAM_SELF_CONTRADICTION else "registered" if known else "unclassified"
        g = by_key[key]
        g["count"] += 1
        if len(g["samples"]) < SAMPLES_PER_SOURCE:
            g["samples"].append({"metric_id": r["metric_id"], "check_id": r["check_id"],
                                 "detail": r["detail"], "known_issue": known or None})
    return groups


def lineage_graph() -> dict:
    nodes: dict[str, dict] = {}
    edges: set[tuple[str, str]] = set()

    def add(node_id: str, label: str, kind: str, ref: str | None = None) -> str:
        nodes.setdefault(node_id, {"id": node_id, "label": label, "kind": kind, "ref": ref,
                                   "status": None})
        return node_id

    for ds in DATASETS.values():
        add(f"dataset:{ds.id}", ds.name, str(ds.layer), ds.id)
    for ds in DATASETS.values():
        target = f"dataset:{ds.id}"
        for src in ds.sources:
            if src in DATASETS:
                edges.add((f"dataset:{src}", target))
                continue
            s = SOURCES.get(src)
            label = f"{s.provider} {s.api}" if s else src
            edges.add((add(f"source:{src}", label, "source", src), target))
        for consumer in ds.consumers:
            if consumer in DATASETS:
                edges.add((target, f"dataset:{consumer}"))
            else:
                edges.add((target, add(f"consumer:{consumer}", consumer, "consumer", consumer)))
    for dep in DEPENDENCIES:
        edges.add((f"dataset:{dep.upstream}", f"dataset:{dep.downstream}"))
    valid = [e for e in sorted(edges) if e[0] in nodes and e[1] in nodes]
    return {"nodes": list(nodes.values()), "edges": [{"source": a, "target": b} for a, b in valid]}


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    return v


def table_sample(root: Path, dataset: Dataset, limit: int = SAMPLE_LIMIT) -> dict:
    out: dict[str, Any] = {"available": False, "message": "", "file": None, "row_group": None,
                           "columns": [], "rows": [], "limit": limit}
    if not dataset.physical:
        out["message"] = "逻辑视图没有物理文件，无法抽样"
        return out
    base = Path(root) / dataset.id
    parts = sorted(base.glob("year=*/part.parquet"))
    target = parts[-1] if parts else base / "part.parquet"
    if not target.is_file():
        out["message"] = "仓库里没有这张表的文件"
        return out
    pf = pq.ParquetFile(target)
    out["file"] = str(target.relative_to(Path(root)))
    out["columns"] = list(pf.schema_arrow.names)
    if pf.metadata.num_row_groups == 0:
        out.update(available=True, message="文件为空")
        return out
    last = pf.metadata.num_row_groups - 1
    out["row_group"] = last
    batch = next(pf.iter_batches(batch_size=limit, row_groups=[last]), None)
    rows = batch.to_pylist() if batch is not None else []
    out["rows"] = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
    out["available"] = True
    return out
