"""域总览与域详情的只读视图模型。

这个页面的全部价值：把 `SourceStatus`（在采集 / 已停采仍在读 / 已失效）
摆到眼前，并让「表内写什么别名」与「目录登记什么 id」在同一行并列——
2026-10-05 用户两次因为「看不见」卡住，根因都在这里。
"""

from __future__ import annotations

from dataclasses import dataclass

from qsys.data.catalog import DATASETS
from qsys.data.catalog.domains import (
    DomainRow,
    SourceEntryKind,
    UnclassifiedRow,
    classify_entry,
    dataset_root_sources,
    domain_summary,
    unclassified_summary,
)
from qsys.data.catalog.sources import SOURCES
from qsys.data.catalog.types import DataDomain, Dataset, Unclassified

DOMAIN_LABELS: dict[str, str] = {
    "reference": "基础 Reference",
    "market": "行情 Market",
    "fundamental": "基本面 Fundamental",
    "factor": "衍生 Factor",
    "macro": "宏观 Macro",
    "alternative": "另类 Alternative",
    "runtime": "运行与质检 Runtime",
}

STATUS_LABELS: dict[str, str] = {
    "active": "在采集",
    "frozen": "已停采，历史仍在读",
    "dead": "上游已失效",
}

# 已知可得但未接入。依赖 Tushare 积分权限这一外部事实，测试验不了，
# 故带登记日期：日期旧了自然会被怀疑，这比假装能验的测试诚实。
NOT_INGESTED: dict[str, tuple[str, str]] = {
    "macro": ("Tushare Pro 当前积分已含宏观经济接口。", "2026-10-05"),
    "alternative": ("龙虎榜、概念板块、北向在当前积分内可得但尚未接入。", "2026-10-10"),
}


@dataclass(frozen=True, slots=True)
class SourceRef:
    """`Dataset.sources` 的一项，按能不能在 `SOURCES`/`DATASETS` 里查到分类。

    这个字段本身是混的：源 id、另一张表的 id、纯自由文本（如「显式异常
    覆盖」）都写在同一个 tuple 里。详情页曾经把三者原样 join 成一行显示
    成「根源」，结果 `bar_minute_1m.sources` 是
    `('paid_minute_bars', 'tushare_minute_bars', 'joinquant_minute_bars',
    'minute_bars')`——四个表 id
    配一句备注，一个源字符串都没有，而它正是触发本任务的那张表。
    这里只分类、不新增推导：命中 `SOURCES` 才当源渲染。
    """

    kind: str          # "source" | "table" | "note"
    label: str
    href: str = ""
    status: str = ""   # 仅 kind == "source" 时有值


def classify_sources(entries: tuple[str, ...]) -> tuple[SourceRef, ...]:
    """身份判据来自 `catalog.domains.classify_entry`，与血缘递归共用一处。"""
    refs = []
    for entry in entries:
        kind = classify_entry(entry)
        if kind is SourceEntryKind.SOURCE:
            source = SOURCES[entry]
            refs.append(SourceRef(
                kind="source",
                label=f"{source.provider}·{source.api}",
                status=str(source.status),
            ))
        elif kind is SourceEntryKind.TABLE:
            refs.append(SourceRef(
                kind="table",
                label=f"表：{entry}",
                href=f"/data/tables/{entry}",
            ))
        else:
            refs.append(SourceRef(kind="note", label=entry))
    return tuple(refs)


def root_source_refs(dataset_id: str) -> tuple[SourceRef, ...]:
    """这张表递归到底的根源接口。`登记来源` 停在一跳，这一格才回答「数据从哪来」。"""
    return classify_sources(dataset_root_sources(dataset_id))


def domain_label(domain: DataDomain | Unclassified) -> str:
    """域的页面措辞。未归类的表必须连理由一起说出来，不能只是不出现。"""
    if isinstance(domain, DataDomain):
        return DOMAIN_LABELS[str(domain)]
    if isinstance(domain, Unclassified):
        return f"未归类：{domain.reason}"
    return "未归类"


@dataclass(frozen=True, slots=True)
class DatasetRow:
    dataset: Dataset
    source_refs: tuple[SourceRef, ...]
    root_refs: tuple[SourceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class DomainDetail:
    row: DomainRow
    label: str
    dataset_rows: tuple[DatasetRow, ...]


class DomainService:
    """域总览/详情的只读视图；数据全部从目录代码现算，不依赖仓库。"""

    @staticmethod
    def rows() -> tuple[DomainRow, ...]:
        return domain_summary()

    @staticmethod
    def total_datasets() -> int:
        """七域 + 未归类必须等于这个数；对不上说明又漏了一类。"""
        return len(DATASETS)

    @staticmethod
    def unclassified() -> UnclassifiedRow:
        """七域之外的那一行。空的时候也返回，页面据此决定显不显示。"""
        return unclassified_summary()

    @staticmethod
    def detail(domain: str) -> DomainDetail | None:
        try:
            target = DataDomain(domain)
        except ValueError:
            return None
        row = next(r for r in domain_summary() if r.domain is target)
        dataset_rows = tuple(
            DatasetRow(
                dataset=ds,
                source_refs=classify_sources(ds.sources),
                root_refs=root_source_refs(ds.id),
            )
            for ds in row.datasets
        )
        return DomainDetail(
            row=row, label=DOMAIN_LABELS[str(target)], dataset_rows=dataset_rows
        )
