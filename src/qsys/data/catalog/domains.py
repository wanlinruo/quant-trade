"""域视图：按业务分类聚合数据集与其根源。

域→源**算出来、不手工填**：手工填会和 `Dataset.sources` 漂移，而漂移正是
2026-10-05 那次「以为付费源已停用」的成因之一。

算法是 spec §3.3 的 `domain → datasets → root_sources()`：**递归到源叶子**。
只走一跳会把多张表显示成「无源」——包括 `bar_minute_1m`（上游写的是
`paid_minute_bars`/`minute_bars` 两张表）和 `bar_daily`（上游写的是
`tushare_daily`）。写上游表 id 是本仓库的既定写法，不是缺陷，所以要递归，
不是改数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from qsys.data.catalog import DATASETS
from qsys.data.catalog.sources import SOURCES
from qsys.data.catalog.types import DataDomain, Dataset, Source, Unclassified


class SourceEntryKind(StrEnum):
    """`Dataset.sources` 一项的三种身份。

    这个字段本身是混的：源 id、另一张表的 id、纯自由文本（如「显式异常
    覆盖」）都写在同一个 tuple 里。判据只有一处（这里），血缘递归与页面
    渲染共用——两边各判一次必然在加源/改名时漂移。
    """

    SOURCE = "source"
    TABLE = "table"
    NOTE = "note"


def classify_entry(entry: str) -> SourceEntryKind:
    """只分类、不新增推导：命中 `SOURCES` 才算源，命中 `DATASETS` 才算表。"""
    if entry in SOURCES:
        return SourceEntryKind.SOURCE
    if entry in DATASETS:
        return SourceEntryKind.TABLE
    return SourceEntryKind.NOTE


def dataset_root_sources(
    dataset_id: str, _seen: frozenset[str] = frozenset()
) -> tuple[str, ...]:
    """沿 `Dataset.sources` 递归到源叶子，给出这张表的**根源接口** id。

    与 `qsys.data.catalog.root_sources`（指标级，沿 `Computed.upstream` 走）
    同一个思路的表级版本。`_seen` 防环：当前注册表无环，但「无环」是事实
    不是约束，加一张表就可能不成立，而无环假设一旦破掉就是无限递归。

    自由文本项解不出源，按定义丢弃——编一个听起来合理的源才是违规。
    """
    if dataset_id in _seen or dataset_id not in DATASETS:
        return ()
    seen = _seen | {dataset_id}
    out: set[str] = set()
    for entry in DATASETS[dataset_id].sources:
        kind = classify_entry(entry)
        if kind is SourceEntryKind.SOURCE:
            out.add(entry)
        elif kind is SourceEntryKind.TABLE:
            out.update(dataset_root_sources(entry, seen))
    return tuple(sorted(out))


def _sources_of(datasets: tuple[Dataset, ...]) -> tuple[Source, ...]:
    ids = sorted({s for d in datasets for s in dataset_root_sources(d.id)})
    return tuple(SOURCES[i] for i in ids)


@dataclass(frozen=True, slots=True)
class DomainRow:
    domain: DataDomain
    datasets: tuple[Dataset, ...]
    sources: tuple[Source, ...]


@dataclass(frozen=True, slots=True)
class UnclassifiedRow:
    """归不进任何业务域的表。

    spec §3.2 为「必填无默认」付的代价，换来的承诺是「页面上永远不会有归属
    不明的表」。只按 `for domain in DataDomain` 遍历会把它实现成「归属不明的
    表不会在页面上」——字面满足、意图相反。所以单独算一行，空的时候也返回，
    由页面决定要不要显示。
    """

    datasets: tuple[Dataset, ...]
    sources: tuple[Source, ...]

    @property
    def reasons(self) -> tuple[tuple[str, str], ...]:
        """(表 id, 登记的理由)。理由必须和表一起显示，否则等于没登记。"""
        return tuple(
            (d.id, d.domain.reason)
            for d in self.datasets
            if isinstance(d.domain, Unclassified)
        )


def domain_summary() -> tuple[DomainRow, ...]:
    """七个域各一行，空域也出现。"""
    rows = []
    for domain in DataDomain:
        datasets = tuple(
            d for d in DATASETS.values()
            if isinstance(d.domain, DataDomain) and d.domain is domain
        )
        rows.append(DomainRow(domain, datasets, _sources_of(datasets)))
    return tuple(rows)


def unclassified_summary() -> UnclassifiedRow:
    """`domain_summary()` 漏掉的那些表——合起来才是全部 `DATASETS`。"""
    datasets = tuple(
        d for d in DATASETS.values() if not isinstance(d.domain, DataDomain)
    )
    return UnclassifiedRow(datasets, _sources_of(datasets))
