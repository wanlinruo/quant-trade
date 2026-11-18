"""数据组对外契约：数据资产、指标字典、数据质量（#662 R4，设计文档 §8.13）。

只输出代码与目录登记的事实；状态、维度、域、指标状态等的中文名由前端映射。
灰（gray）表示缺证据，不当正常。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import JsonValue

from qsys.webapp.contracts.base import Contract, Timestamp

Health = Literal["red", "yellow", "green", "gray"]
Dimension = Literal["freshness", "completeness", "structure", "validity", "lineage"]


class DataDisabled(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[False] = False


class DomainRef(Contract):
    code: str  # DataDomain 值；未归类为 "unclassified"
    unclassified_reason: str | None


class SourceRef(Contract):
    kind: Literal["source", "table", "note"]
    id: str
    provider: str | None
    api: str | None
    status: Literal["active", "frozen", "dead"] | None


class Coverage(Contract):
    rows: int
    symbols: int
    first_dt: Timestamp | date | None
    last_dt: Timestamp | date | None
    measured_at: Timestamp | None


class DataTableRow(Contract):
    id: str
    name: str
    layer: str
    domain: DomainRef
    status: Health
    purpose: str
    grain: str
    coverage: Coverage | None
    coverage_state: Literal["ready", "pending", "failed", "none"]


class DataTableList(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    rows: list[DataTableRow]


class DatasetContract(Contract):
    id: str
    name: str
    purpose: str
    layer: str
    role: str
    status: str
    grain: str
    primary_key: list[str]
    time_columns: list[str]
    partition_keys: list[str]
    sort_keys: list[str]
    schema_id: str | None
    schema_version: int  # 数据集自身的 schema 版本
    compatibility: str
    builder: str
    update_mode: str
    finalized_at: str
    lifecycle: str
    sources: list[str]
    consumers: list[str]
    health_policy: str | None
    domain: DomainRef
    physical: bool


class DependencyInfo(Contract):
    upstream: str
    downstream: str
    kind: str
    join_keys: list[tuple[str, str]]
    cardinality: str
    requirement: str
    allowed_lag: str
    metrics: list[str]
    health_policy: str | None
    note: str


class HealthReasonInfo(Contract):
    code: str
    message: str
    dimension: Dimension | None
    check_id: str | None


class FieldInfo(Contract):
    name: str
    type: str
    meaning: str | None


class DataTableDetail(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    row: DataTableRow
    contract: DatasetContract
    sources: list[SourceRef]
    root_sources: list[SourceRef]
    dimensions: dict[Dimension, Health]
    reasons: list[HealthReasonInfo]
    evidence: list[dict[str, JsonValue]]
    upstream: list[DependencyInfo]
    downstream: list[DependencyInfo]
    fields: list[FieldInfo]
    rules: list[str]


class TableSample(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    available: bool
    reason: Literal["ok", "logical_view", "no_file", "empty_file", "read_failed"]
    file: str | None
    row_group: int | None
    columns: list[str]
    rows: list[dict[str, JsonValue]]
    limit: int


class OverviewTable(Contract):
    id: str
    name: str
    layer: str
    domain: DomainRef
    status: Health
    dimensions: dict[Dimension, Health]


class DomainSummary(Contract):
    domain: DomainRef
    tables: int
    sources: int
    status_counts: dict[Health, int]


class LineageNode(Contract):
    id: str
    label: str
    kind: str
    ref: str | None
    status: Health | None


class LineageEdge(Contract):
    source: str
    target: str


class Lineage(Contract):
    nodes: list[LineageNode]
    edges: list[LineageEdge]


class DataOverview(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    tables: list[OverviewTable]
    domains: list[DomainSummary]
    lineage: Lineage


# ---- 指标字典 ----
MetricStatus = Literal["failed", "unknown", "uncovered", "unverified", "known_issue", "ok"]


class DirectLineage(Contract):
    source_id: str
    provider: str | None
    api: str | None
    field: str
    multiplier: float


class MetricRow(Contract):
    id: str
    dataset: str
    column: str
    kind: str
    validity: str
    meaning: str | None  # 含义未登记为 None
    direct: DirectLineage | None  # 直取指标的接口与字段；派生指标为 None
    roots: list[str]  # 递归追溯到的根源接口
    status: MetricStatus
    issues: list[str]


class CatalogSummary(Contract):
    total: int
    by_kind: dict[str, int]
    covered: int
    pending: int
    latest_run: Timestamp | None


class MetricList(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    summary: CatalogSummary
    rows: list[MetricRow]
    datasets: list[str]
    kinds: list[str]


class MetricCheckRow(Contract):
    run_id: str
    checked_at: Timestamp | None
    metric_id: str
    check_id: str
    category: str | None
    scope_start: date | None
    scope_end: date | None
    passed: bool | None
    severity: str | None
    known_issue: str | None
    violations: int | None
    checked_rows: int | None
    detail: str | None
    sample_file: str | None  # "<run_id>/<check_id>.parquet"，可经质检样本接口下载


class MetricEvidence(Contract):
    metric_id: str
    symbol: str | None
    dt: date | None
    our_value: str | None
    reference_value: str | None
    reference_source: str | None
    verdict: str | None
    checked_by: str | None
    checked_at: Timestamp | None
    note: str | None


class MetricDetail(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    row: MetricRow
    upstream: list[str]
    downstream: list[str]
    ext_consumers: list[str]
    formula: str | None
    params: dict[str, JsonValue]
    checks: list[MetricCheckRow]
    history: list[MetricCheckRow]
    evidence: list[MetricEvidence]


# ---- 数据质量 ----
RuleStatus = Literal["blocking", "warning", "unknown", "passed"]


class FieldCounts(Contract):
    total: int
    passed: int
    blocking: int
    warning: int


class QualityRun(Contract):
    run_id: str
    checked_at: Timestamp | None
    fields: FieldCounts
    tables: dict[Health, int] | None  # 该 run 没有表级记录时为 None，不当 0


class WarningSample(Contract):
    metric_id: str
    check_id: str
    detail: str | None
    known_issue: str | None


class WarningSource(Contract):
    key: Literal["upstream", "registered", "unclassified"]
    issue: str | None
    count: int
    samples: list[WarningSample]


class RuleRow(Contract):
    id: str
    name: str
    category: str
    scope: str
    known_issues: list[str]
    upstream_self_consistency: bool
    latest_status: RuleStatus


class QualitySummary(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    runs: list[QualityRun]
    warning_sources: list[WarningSource]
    rules: list[RuleRow]


class RuleResult(Contract):
    """字段级 / 表级 / 关系级质检记录的并集；不适用的字段为 None。"""

    run_id: str | None
    checked_at: Timestamp | None
    metric_id: str | None
    dataset_id: str | None
    upstream: str | None
    downstream: str | None
    check_id: str
    category: str | None
    dimension: str | None
    scope_start: date | None
    scope_end: date | None
    passed: bool | None
    severity: str | None
    known_issue: str | None
    status: str | None
    evidence_at: Timestamp | None
    expires_at: Timestamp | None
    required: bool | None
    violations: int | None
    checked_rows: int | None
    upstream_rows: int | None
    downstream_rows: int | None
    matched_rows: int | None
    orphan_rows: int | None
    detail: str | None
    input_version: str | None
    sample_file: str | None


class RuleDetail(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    id: str
    name: str
    category: str
    condition: str
    limitation: str
    implementation: str
    targets: list[str]
    latest: list[RuleResult]
    history: list[RuleResult]
