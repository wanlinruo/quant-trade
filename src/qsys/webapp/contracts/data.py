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
