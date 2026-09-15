"""指标字典的聚合入口。

条目分散在五个模块里（各 20–90 条）。不放一个文件：2000 行的 registry
没人会读，没人读的字典就会开始烂。

这里只做三件事：把条目并起来、把「谁认领了哪张表」汇总、把 consumers
和根源从血缘反向推导出来（手写的双向关系必然漂移）。
"""

from __future__ import annotations

from qsys.data import schema
from qsys.data.catalog.sources import SOURCES
from qsys.data.catalog.types import (
    Computed,
    Direct,
    Kind,
    Metric,
    MetricStatus,
    Source,
    Uncovered,
    Validity,
)

# Task 4-8 逐个接上；每个模块导出 (METRICS, DATASETS) 两个名字。
_MODULES: list[str] = ["market", "reference", "derived", "factors"]

_metrics: dict[str, Metric] = {}
_owned: set[str] = set()

for _name in _MODULES:
    _mod = __import__(f"qsys.data.catalog.{_name}", fromlist=["METRICS", "DATASETS"])
    for _m in _mod.METRICS:
        if _m.id in _metrics:
            raise ValueError(f"重复登记的指标：{_m.id}")
        _metrics[_m.id] = _m
    _overlap = _owned & set(_mod.DATASETS)
    if _overlap:
        raise ValueError(f"{_name} 与其他模块重复认领：{_overlap}")
    _owned.update(_mod.DATASETS)

REGISTRY: dict[str, Metric] = _metrics
OWNED_DATASETS: frozenset[str] = frozenset(_owned)

__all__ = [
    "REGISTRY", "SOURCES", "OWNED_DATASETS", "expected_metric_ids",
    "landed", "unlanded", "consumers", "root_sources",
    "Metric", "Source", "Direct", "Computed",
    "Kind", "Validity", "MetricStatus", "Uncovered",
]


def expected_metric_ids() -> set[str]:
    """字典**应该**有哪些落盘条目：schema.ALL 展开 + PAID_DAILY 展开。"""
    ids = {
        f"{dataset}.{col}"
        for dataset, arrow_schema in schema.ALL.items()
        for col in arrow_schema.names
    }
    ids |= {f"paid_daily.{col}" for col in schema.PAID_DAILY.names}
    return ids


def landed() -> dict[str, Metric]:
    return {k: m for k, m in REGISTRY.items() if m.landed}


def unlanded() -> dict[str, Metric]:
    """策略侧不落盘的决策量。它们是 S6/S7 Gate 的直接输入。"""
    return {k: m for k, m in REGISTRY.items() if not m.landed}


def consumers(metric_id: str) -> tuple[str, ...]:
    """下游哪些**指标**在吃它。由血缘反向推导，不手写——
    手写的双向关系必然漂移。"""
    out = [
        other.id
        for other in REGISTRY.values()
        if isinstance(other.lineage, Computed) and metric_id in other.lineage.upstream
    ]
    return tuple(sorted(out))


def root_sources(metric_id: str, _seen: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """沿血缘走到 DIRECT 叶子，给出这个指标的**根源**集合。

    页面用它回答「首板这个数到底从哪来的」——答案要给到接口级，
    不能停在「Tushare」。
    """
    if metric_id in _seen or metric_id not in REGISTRY:
        return ()
    metric = REGISTRY[metric_id]
    if isinstance(metric.lineage, Direct):
        return (f"{metric.lineage.source_id}.{metric.lineage.field}",)
    seen = _seen | {metric_id}
    out: set[str] = set()
    for up in metric.lineage.upstream:
        out.update(root_sources(up, seen))
    return tuple(sorted(out))
