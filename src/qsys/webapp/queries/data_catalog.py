"""指标字典查询（#662 R4，设计文档 §8.13）：直接使用指标注册表 REGISTRY 与质检、验真记录。

指标状态判定与旧 CatalogService 相同（含义未登记 → unknown；未覆盖 → uncovered；没有结果 → unverified；
有阻断失败 → failed；只有非阻断失败 → known_issue；否则 ok），只输出代码，文字由前端映射。
"""

from __future__ import annotations

from pathlib import PurePath

from qsys.data.catalog import REGISTRY, SOURCES, consumers, root_sources
from qsys.data.catalog.types import UNKNOWN, Computed, Direct, Kind, Uncovered
from qsys.data.query import WarehouseQuery
from qsys.webapp.contracts.data import (
    CatalogSummary, DirectLineage, MetricCheckRow, MetricDetail, MetricEvidence, MetricList, MetricRow,
)
from qsys.webapp.queries.data_assets import json_value

ORDER = ("failed", "unknown", "uncovered", "unverified", "known_issue", "ok")
STATUSES = frozenset(ORDER)


def sample_file(row: dict) -> str | None:
    """记录的 sample_path 最后两级恰为 <run_id>/<check_id>.parquet 时给出相对名，否则 None。"""
    path = row.get("sample_path")
    if not path:
        return None
    parts = PurePath(str(path)).parts
    rel = parts[-2:] if len(parts) >= 2 else ()
    if rel != (str(row.get("run_id")), f"{row.get('check_id')}.parquet"):
        return None
    return "/".join(rel)


def check_row(row: dict) -> MetricCheckRow:
    data = {k: row.get(k) for k in MetricCheckRow.model_fields if k != "sample_file"}
    return MetricCheckRow(**data, sample_file=sample_file(row))


def _direct(metric) -> DirectLineage | None:
    if not isinstance(metric.lineage, Direct):
        return None
    s = SOURCES.get(metric.lineage.source_id)
    return DirectLineage(source_id=metric.lineage.source_id, provider=s.provider if s else None,
                         api=s.api if s else None, field=metric.lineage.field,
                         multiplier=float(metric.lineage.multiplier))


def _status(metric, results: list[dict] | None) -> str:
    if metric.meaning is UNKNOWN:
        return "unknown"
    if isinstance(metric.checks, Uncovered):
        return "uncovered"
    if not results:
        return "unverified"
    failed = [r for r in results if not r["passed"]]
    if any(r["severity"] == "blocking" for r in failed):
        return "failed"
    return "known_issue" if failed else "ok"


class DataCatalog:
    def __init__(self, query: WarehouseQuery) -> None:
        self._query = query

    def _latest(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for row in self._query.latest_metric_checks().iter_rows(named=True):
            out.setdefault(row["metric_id"], []).append(row)
        return out

    @staticmethod
    def datasets() -> list[str]:
        return sorted({m.dataset for m in REGISTRY.values()})

    @staticmethod
    def kinds() -> list[str]:
        return [str(k) for k in Kind]

    def rows(self, dataset: str | None = None, kind: str | None = None,
             status: str | None = None) -> list[MetricRow]:
        latest = self._latest()
        rows = []
        for mid, m in REGISTRY.items():
            results = latest.get(mid)
            issues = list(m.known_issues)
            for r in results or ():
                if r["known_issue"] and r["known_issue"] not in issues:
                    issues.append(r["known_issue"])
            rows.append(MetricRow(
                id=mid, dataset=m.dataset, column=m.column, kind=str(m.kind), validity=str(m.validity),
                meaning=None if m.meaning is UNKNOWN else m.meaning, direct=_direct(m),
                roots=list(root_sources(mid)), status=_status(m, results), issues=issues))
        rows = [r for r in rows if (not dataset or r.dataset == dataset) and (not kind or r.kind == kind)
                and (status is None or r.status == status)]
        return sorted(rows, key=lambda r: (ORDER.index(r.status), r.id))

    def summary(self, rows: list[MetricRow] | None = None) -> CatalogSummary:
        rows = self.rows() if rows is None else rows
        checks = self._query.latest_metric_checks()
        return CatalogSummary(
            total=len(rows), by_kind={str(k): sum(r.kind == str(k) for r in rows) for k in Kind},
            covered=sum(r.status in ("ok", "failed", "known_issue") for r in rows),
            pending=sum(r.status in ("failed", "unknown", "uncovered") for r in rows),
            latest_run=checks["checked_at"].max() if checks.height else None)

    def list(self, dataset: str | None = None, kind: str | None = None, status: str | None = None) -> MetricList:
        return MetricList(summary=self.summary(), rows=self.rows(dataset, kind, status),
                          datasets=self.datasets(), kinds=self.kinds())

    def detail(self, metric_id: str) -> MetricDetail | None:
        m = REGISTRY.get(metric_id)
        if m is None:
            return None
        row = next(r for r in self.rows() if r.id == metric_id)
        computed = isinstance(m.lineage, Computed)
        return MetricDetail(
            row=row, upstream=list(m.lineage.upstream) if computed else [],
            downstream=list(consumers(metric_id)), ext_consumers=list(m.ext_consumers),
            formula=m.lineage.formula if computed else None,
            params={str(k): json_value(v) for k, v in (m.lineage.params if computed else ())},
            checks=[check_row(r) for r in self._latest().get(metric_id, [])],
            history=[check_row(r) for r in self._query.metric_checks(metric_id=metric_id).to_dicts()],
            evidence=[MetricEvidence(**{k: r.get(k) for k in MetricEvidence.model_fields})
                      for r in self._query.metric_evidence(metric_id).to_dicts()])
