"""指标字典与自动验证结果的只读视图模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from qsys.data.catalog import REGISTRY, SOURCES, root_sources
from qsys.data.catalog.types import UNKNOWN, Direct, Kind, Uncovered
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


class Status(StrEnum):
    FAILED = "failed"
    UNKNOWN_MEANING = "unknown"
    UNCOVERED = "uncovered"
    UNVERIFIED = "unverified"
    KNOWN_ISSUE = "known_issue"
    OK = "ok"


_ORDER = {
    Status.FAILED: 0,
    Status.UNKNOWN_MEANING: 1,
    Status.UNCOVERED: 2,
    Status.UNVERIFIED: 3,
    Status.KNOWN_ISSUE: 4,
    Status.OK: 5,
}

_LABEL = {
    Status.FAILED: ("失败", "bad"),
    Status.UNKNOWN_MEANING: ("含义待查", "todo"),
    Status.UNCOVERED: ("未覆盖", "todo"),
    Status.UNVERIFIED: ("未验证", "todo"),
    Status.KNOWN_ISSUE: ("已知缺陷", "warn"),
    Status.OK: ("通过", "ok"),
}


@dataclass(frozen=True, slots=True)
class MetricRow:
    id: str
    dataset: str
    column: str
    kind: Kind
    validity: str
    meaning: str
    source_label: str
    status: Status
    issues: tuple[str, ...]

    @property
    def label(self) -> str:
        return _LABEL[self.status][0]

    @property
    def pill(self) -> str:
        return _LABEL[self.status][1]


@dataclass(frozen=True, slots=True)
class CatalogSummary:
    total: int
    by_kind: tuple[tuple[str, int], ...]
    covered: int
    pending: int
    latest_run: datetime | None

    @property
    def coverage_rate(self) -> str:
        return f"{self.covered / self.total * 100:.0f}%" if self.total else "—"


def _source_label(metric_id: str) -> str:
    """直取显示接口与字段，派生显示递归追溯得到的根源集合。"""
    metric = REGISTRY[metric_id]
    if isinstance(metric.lineage, Direct):
        source = SOURCES.get(metric.lineage.source_id)
        provider = source.provider if source else metric.lineage.source_id
        api = source.api if source else "?"
        multiplier = metric.lineage.multiplier
        suffix = f" ×{multiplier:g}" if multiplier != 1.0 else ""
        return f"{provider}·{api}.{metric.lineage.field}{suffix}"
    roots = root_sources(metric_id)
    return " / ".join(roots) if roots else "（无法追溯到直取源）"


class CatalogService:
    def __init__(self, warehouse: Warehouse) -> None:
        self._query = WarehouseQuery(warehouse)

    def _latest(self) -> dict[str, list[dict]]:
        frame = self._query.latest_metric_checks()
        results: dict[str, list[dict]] = {}
        for row in frame.iter_rows(named=True):
            results.setdefault(row["metric_id"], []).append(row)
        return results

    @staticmethod
    def _status(metric_id: str, results: list[dict] | None) -> Status:
        metric = REGISTRY[metric_id]
        if metric.meaning is UNKNOWN:
            return Status.UNKNOWN_MEANING
        if isinstance(metric.checks, Uncovered):
            return Status.UNCOVERED
        if not results:
            return Status.UNVERIFIED
        failed = [result for result in results if not result["passed"]]
        if any(result["severity"] == "blocking" for result in failed):
            return Status.FAILED
        if failed:
            return Status.KNOWN_ISSUE
        return Status.OK

    def rows(
        self,
        *,
        dataset: str | None = None,
        kind: str | None = None,
        status: Status | None = None,
    ) -> list[MetricRow]:
        latest = self._latest()
        rows: list[MetricRow] = []
        for metric_id, metric in REGISTRY.items():
            results = latest.get(metric_id)
            issues = list(metric.known_issues)
            for result in results or ():
                issue = result["known_issue"]
                if issue and issue not in issues:
                    issues.append(issue)
            rows.append(MetricRow(
                id=metric_id,
                dataset=metric.dataset,
                column=metric.column,
                kind=metric.kind,
                validity=str(metric.validity),
                meaning=metric.meaning,
                source_label=_source_label(metric_id),
                status=self._status(metric_id, results),
                issues=tuple(issues),
            ))
        if dataset:
            rows = [row for row in rows if row.dataset == dataset]
        if kind:
            rows = [row for row in rows if str(row.kind) == kind]
        if status is not None:
            rows = [row for row in rows if row.status is status]
        return sorted(rows, key=lambda row: (_ORDER[row.status], row.id))

    def summary(self) -> CatalogSummary:
        rows = self.rows()
        by_kind = tuple(
            (str(kind), sum(row.kind is kind for row in rows)) for kind in Kind
        )
        covered = sum(
            row.status in (Status.OK, Status.FAILED, Status.KNOWN_ISSUE)
            for row in rows
        )
        pending = sum(
            row.status in (
                Status.FAILED, Status.UNKNOWN_MEANING, Status.UNCOVERED
            )
            for row in rows
        )
        checks = self._query.latest_metric_checks()
        latest_run = checks["checked_at"].max() if checks.height else None
        return CatalogSummary(len(rows), by_kind, covered, pending, latest_run)

    @staticmethod
    def datasets() -> list[str]:
        return sorted({metric.dataset for metric in REGISTRY.values()})

    @staticmethod
    def kinds() -> list[str]:
        return [str(kind) for kind in Kind]

    @staticmethod
    def statuses() -> list[tuple[str, str]]:
        return [(str(status), _LABEL[status][0]) for status in Status]
