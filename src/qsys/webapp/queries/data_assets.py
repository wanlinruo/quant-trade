"""数据资产查询（#662 R4，设计文档 §8.13）：数据总览、表清单、表详情、表样本。

直接使用 ``qsys.data.catalog`` 的领域对象与 ``WarehouseQuery``，只输出代码与目录登记的事实：
- 健康评估：最新持久化证据（经 ``current_result`` 核对输入版本）+ ``aggregate_health``；
- 缺证据的维度一律 gray，总体状态由领域聚合给出；
- 覆盖统计：后台刷新口径沿用旧服务（pending / failed，失败 30 秒后重试），读不到为 None。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from threading import Lock, Thread
from time import monotonic
from typing import Any

from qsys.data import schema
from qsys.data.catalog import DATASETS, DEPENDENCIES, REGISTRY, SOURCES
from qsys.data.catalog.domains import SourceEntryKind, classify_entry, dataset_root_sources
from qsys.data.catalog.health import (
    ALL_DIMENSIONS, HealthDimension, HealthEvidence, HealthReason, HealthStatus, aggregate_health, policy_for,
)
from qsys.data.catalog.input_versions import current_result
from qsys.data.catalog.types import UNKNOWN, DataDomain, Dataset, DatasetDependency, DatasetLayer
from qsys.data.coverage import cached_coverage, coverage, previous_coverage
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.webapp.contracts.base import to_aware
from qsys.webapp.contracts.data import (
    Coverage, DatasetContract, DataOverview, DataTableDetail, DataTableList, DataTableRow, DependencyInfo,
    DomainRef, DomainSummary, FieldInfo, HealthReasonInfo, Lineage, OverviewTable, SourceRef, TableSample,
)
from qsys.webapp.queries.data_derive import lineage_graph, table_sample

log = logging.getLogger(__name__)
_ORDER = {HealthStatus.RED: 0, HealthStatus.YELLOW: 1, HealthStatus.GRAY: 2, HealthStatus.GREEN: 3}
RETRY_SECONDS = 30


def json_value(v: Any) -> Any:
    """持久化证据行里的值 → JSON：无时区时间按 Asia/Shanghai 标注，日期取 ISO，字节取十六进制。"""
    if isinstance(v, datetime):
        return to_aware(v).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, (list, tuple)):
        return [json_value(x) for x in v]
    if isinstance(v, dict):
        return {str(k): json_value(x) for k, x in v.items()}
    return v


def json_row(row: dict) -> dict[str, Any]:
    return {str(k): json_value(v) for k, v in row.items()}


def domain_ref(domain) -> DomainRef:
    if isinstance(domain, DataDomain):
        return DomainRef(code=str(domain), unclassified_reason=None)
    return DomainRef(code="unclassified", unclassified_reason=getattr(domain, "reason", None))


def source_refs(entries) -> list[SourceRef]:
    out = []
    for entry in entries:
        kind = classify_entry(entry)
        if kind is SourceEntryKind.SOURCE:
            s = SOURCES[entry]
            out.append(SourceRef(kind="source", id=entry, provider=s.provider, api=s.api, status=str(s.status)))
        else:
            out.append(SourceRef(kind="table" if kind is SourceEntryKind.TABLE else "note", id=entry,
                                 provider=None, api=None, status=None))
    return out


def dependency_info(edge: DatasetDependency) -> DependencyInfo:
    return DependencyInfo(
        upstream=edge.upstream, downstream=edge.downstream, kind=str(edge.kind),
        join_keys=[tuple(k) for k in edge.join_keys], cardinality=str(edge.cardinality),
        requirement=str(edge.requirement), allowed_lag=edge.allowed_lag, metrics=list(edge.metrics),
        health_policy=edge.health_policy, note=edge.note)


def dataset_contract(ds: Dataset) -> DatasetContract:
    return DatasetContract(
        id=ds.id, name=ds.name, purpose=ds.purpose, layer=str(ds.layer), role=str(ds.role), status=str(ds.status),
        grain=ds.grain, primary_key=list(ds.primary_key), time_columns=list(ds.time_columns),
        partition_keys=list(ds.partition_keys), sort_keys=list(ds.sort_keys), schema_id=ds.schema_id,
        schema_version=ds.schema_version, compatibility=ds.compatibility, builder=ds.builder,
        update_mode=ds.update_mode, finalized_at=ds.finalized_at, lifecycle=ds.lifecycle,
        sources=list(ds.sources), consumers=list(ds.consumers), health_policy=ds.health_policy,
        domain=domain_ref(ds.domain), physical=ds.physical)


def _latest_rows(rows: list[dict]) -> list[dict]:
    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (str(row.get("dimension", "")), str(row["check_id"]))
        if key not in latest or row["checked_at"] > latest[key]["checked_at"]:
            latest[key] = row
    return list(latest.values())


def _evidence(rows: list[dict]) -> tuple[HealthEvidence, ...]:
    return tuple(HealthEvidence(
        HealthDimension(r["dimension"]), HealthStatus(r["status"]), r["check_id"], r["evidence_at"],
        r["expires_at"], HealthReason("persisted_result", r["detail"], HealthDimension(r["dimension"]),
                                      r["check_id"]),
        r["violations"], r["checked_rows"]) for r in rows)


class DataAssets:
    def __init__(self, warehouse: Warehouse, *, background_coverage: bool = False) -> None:
        self._warehouse = warehouse
        self._query = WarehouseQuery(warehouse)
        self._background = background_coverage
        self._lock = Lock()
        self._coverage_thread: Thread | None = None
        self._coverage_failed = False
        self._retry_at = 0.0

    @property
    def root(self) -> Path:
        return self._warehouse.root

    @property
    def query(self) -> WarehouseQuery:
        return self._query

    # ---- 覆盖统计 ----
    def _refresh(self) -> None:
        try:
            coverage(self._warehouse)
        except Exception:
            log.exception("数据表统计失败")
            with self._lock:
                self._coverage_failed = True
                self._retry_at = monotonic() + RETRY_SECONDS

    def _coverage(self):
        if not self._background:
            return {row.dataset: row for row in coverage(self._warehouse)}, frozenset(), False
        ready, pending = cached_coverage(self._warehouse)
        previous = previous_coverage(self._warehouse, pending)
        with self._lock:
            if (pending and monotonic() >= self._retry_at
                    and (self._coverage_thread is None or not self._coverage_thread.is_alive())):
                self._coverage_failed = False
                self._coverage_thread = Thread(target=self._refresh, name="qsys-table-coverage", daemon=True)
                self._coverage_thread.start()
            failed = self._coverage_failed
        return {row.dataset: row for row in (*previous, *ready)}, pending, failed

    # ---- 健康 ----
    def assessment(self, dataset_id: str, now: datetime | None = None):
        cache: dict = {}
        rows = [current_result(self._warehouse, r, cache=cache)
                for r in _latest_rows(self._query.dataset_checks(dataset_id).to_dicts())]
        return aggregate_health(policy_for(dataset_id), _evidence(rows), now or datetime.now())

    @staticmethod
    def _dimensions(assessment) -> dict[str, str]:
        return {**{str(d): "gray" for d in ALL_DIMENSIONS}, **{str(d): str(s) for d, s in assessment.dimensions}}

    def _row(self, ds: Dataset, status: HealthStatus, cover, pending, failed) -> DataTableRow:
        c = cover.get(ds.id)
        if ds.id in pending:  # 刷新中：有上次结果时一并给出
            state = "failed" if failed else "pending"
        elif c is not None:
            state = "ready"
        else:
            state = "none"
        return DataTableRow(
            id=ds.id, name=ds.name, layer=str(ds.layer), domain=domain_ref(ds.domain), status=str(status),
            purpose=ds.purpose, grain=ds.grain,
            coverage=None if c is None else Coverage(rows=c.rows, symbols=c.symbols, first_dt=c.first_dt,
                                                     last_dt=c.last_dt, measured_at=c.measured_at),
            coverage_state=state)

    # ---- 对外 ----
    def tables(self, include_raw: bool) -> DataTableList:
        cover, pending, failed = self._coverage()
        items = [(ds, self.assessment(ds.id).status) for ds in DATASETS.values()
                 if include_raw or ds.layer is not DatasetLayer.RAW]
        items.sort(key=lambda x: (_ORDER[x[1]], x[0].layer, x[0].id))
        return DataTableList(rows=[self._row(ds, st, cover, pending, failed) for ds, st in items])

    def table(self, dataset_id: str) -> DataTableDetail | None:
        ds = DATASETS.get(dataset_id)
        if ds is None:
            return None
        cover, pending, failed = self._coverage()
        raw = self._query.dataset_checks(dataset_id).to_dicts()
        a = self.assessment(dataset_id)
        arrow = (schema.ALL | schema.READ_ONLY).get(dataset_id)
        meanings = {m.column: m.meaning for m in REGISTRY.values() if m.dataset == dataset_id}
        fields = [FieldInfo(name=f.name, type=str(f.type),
                            meaning=None if meanings.get(f.name) in (None, UNKNOWN) else meanings[f.name])
                  for f in arrow] if arrow else []
        rules = sorted({c for m in REGISTRY.values() if m.dataset == dataset_id for c in m.checks})
        rules.extend(sorted({r["check_id"] for r in raw} - set(rules)))
        return DataTableDetail(
            row=self._row(ds, a.status, cover, pending, failed), contract=dataset_contract(ds),
            sources=source_refs(ds.sources), root_sources=source_refs(dataset_root_sources(dataset_id)),
            dimensions=self._dimensions(a),
            reasons=[HealthReasonInfo(code=r.code, message=r.message,
                                      dimension=None if r.dimension is None else str(r.dimension),
                                      check_id=r.check_id) for r in a.reasons],
            evidence=[json_row(r) for r in raw],
            upstream=[dependency_info(e) for e in DEPENDENCIES if e.downstream == dataset_id],
            downstream=[dependency_info(e) for e in DEPENDENCIES if e.upstream == dataset_id],
            fields=fields, rules=rules)

    def sample(self, dataset_id: str) -> TableSample | None:
        ds = DATASETS.get(dataset_id)
        if ds is None:
            return None
        try:
            body = table_sample(self.root, ds)
        except Exception as exc:  # 文件损坏等：如实说明不可抽样，不回显路径
            log.info("webapp.data.sample_failed", extra={"error_type": type(exc).__name__})
            body = {"available": False, "reason": "read_failed", "file": None, "row_group": None,
                    "columns": [], "rows": []}
        return TableSample(**{"limit": 20, **body})

    def overview(self) -> DataOverview:
        tables, status_by_id = [], {}
        for ds in DATASETS.values():
            if ds.layer is DatasetLayer.RAW:
                continue
            a = self.assessment(ds.id)
            status_by_id[ds.id] = str(a.status)
            tables.append(OverviewTable(id=ds.id, name=ds.name, layer=str(ds.layer), domain=domain_ref(ds.domain),
                                        status=str(a.status), dimensions=self._dimensions(a)))
        order = {"red": 0, "yellow": 1, "gray": 2, "green": 3}
        tables.sort(key=lambda t: (order[t.status], t.id))
        graph = lineage_graph()
        for n in graph["nodes"]:
            if n["id"].startswith("dataset:"):
                n["status"] = status_by_id.get(n["ref"], "gray")
        domains: dict[str, dict] = {}
        for t in tables:
            d = domains.setdefault(t.domain.code + "|" + (t.domain.unclassified_reason or ""), {
                "domain": t.domain, "tables": 0, "sources": set(),
                "status_counts": dict.fromkeys(("red", "yellow", "gray", "green"), 0)})
            d["tables"] += 1
            d["status_counts"][t.status] += 1
            d["sources"].update(DATASETS[t.id].sources)
        rows = [DomainSummary(domain=d["domain"], tables=d["tables"], sources=len(d["sources"]),
                              status_counts=d["status_counts"]) for d in domains.values()]
        rows.sort(key=lambda d: (d.domain.code, d.domain.unclassified_reason or ""))
        return DataOverview(tables=tables, domains=rows, lineage=Lineage(**graph))
