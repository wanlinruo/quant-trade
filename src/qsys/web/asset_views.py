"""表级数据资产、关系和质量规则的只读视图模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from qsys.data import schema
from qsys.data.catalog import DATASETS, DEPENDENCIES, REGISTRY, SOURCES
from qsys.data.catalog.advisor import (
    PairEvidence,
    SplitEvidence,
    StructureAdvice,
    advise_pair,
    advise_split,
)
from qsys.data.catalog.checks import CHECKS
from qsys.data.catalog.health import (
    HealthDimension,
    HealthEvidence,
    HealthReason,
    HealthStatus,
    aggregate_health,
    policy_for,
)
from qsys.data.catalog.types import (
    UNKNOWN,
    Computed,
    Dataset,
    DatasetDependency,
    DatasetLayer,
    DependencyRequirement,
    JoinCardinality,
    Kind,
)
from qsys.data.coverage import DatasetCoverage, coverage
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.web.domain_views import (
    SourceRef,
    classify_sources,
    domain_label,
    root_source_refs,
)


_STATUS_LABELS = {
    HealthStatus.RED: "红·阻断",
    HealthStatus.YELLOW: "黄·有风险",
    HealthStatus.GREEN: "绿·健康",
    HealthStatus.GRAY: "灰·缺证据",
}
_DIMENSION_LABELS = {
    HealthDimension.FRESHNESS: "时效",
    HealthDimension.COMPLETENESS: "完整",
    HealthDimension.STRUCTURE: "结构",
    HealthDimension.VALIDITY: "真实",
    HealthDimension.LINEAGE: "血缘",
}
_CHECK_METADATA = {
    "T1.table_contract": ("表结构与主键契约", "Schema 一致、主键非空且唯一", "只验证当前检查范围", "qsys.data.catalog.health.check_table_contract"),
    "T2.table_completeness": ("表完整性", "检查范围满足数据集空表与覆盖策略", "覆盖范围由运行器传入", "qsys.data.catalog.health.check_table_contract"),
    "T3.freshness": ("发布时效", "最后发布时间未超过策略 TTL", "依赖准确的发布时间", "qsys.data.catalog.health.freshness_evidence"),
    "T4.field_status": ("字段真实性汇总", "字段级证据无红、黄、灰状态", "汇总质量取决于字段规则覆盖", "qsys.data.catalog.health.field_status_evidence"),
    "T5.frozen_checksum": ("冻结文件校验和", "全部文件与基线校验和一致", "没有基线时只能判为灰色", "qsys.data.catalog.health.frozen_checksum_evidence"),
    "T6.partition_layout": ("分区布局", "分区键、目录与数据值一致", "只检查已发现的物理分区", "qsys.data.catalog.health.partition_layout_evidence"),
    "R1.join_integrity": ("关系完整性", "连接键、基数、孤儿行和刷新顺序符合契约", "只代表记录的检查范围", "qsys.data.catalog.health.check_dependency"),
    "R1.relation_contract": ("关系元数据", "关系键和基数已登记", "不替代实际数据连接检查", "qsys.data.catalog.health.check_dependency"),
    "R2.independent_recompute": ("独立重算", "独立实现与下游产出一致", "需要独立实现和同范围输入", "qsys.data.catalog.health.relation_recompute_evidence"),
}


@dataclass(frozen=True, slots=True)
class DatasetRow:
    dataset: Dataset
    status: HealthStatus
    coverage: DatasetCoverage | None

    @property
    def status_label(self) -> str:
        return _STATUS_LABELS[self.status]


@dataclass(frozen=True, slots=True)
class DatasetDetail:
    row: DatasetRow
    domain_label: str
    source_refs: tuple[SourceRef, ...]
    root_refs: tuple[SourceRef, ...]
    dimensions: tuple[tuple[str, HealthStatus], ...]
    reasons: tuple[HealthReason, ...]
    evidence: tuple[dict, ...]
    upstream: tuple[DatasetDependency, ...]
    downstream: tuple[DatasetDependency, ...]
    fields: tuple[tuple[str, str, str], ...]
    rules: tuple[str, ...]
    suggestion: StructureAdvice
    related_suggestions: tuple[tuple[DatasetDependency, StructureAdvice], ...]


@dataclass(frozen=True, slots=True)
class RelationDetail:
    dependency: DatasetDependency
    latest: dict | None
    history: tuple[dict, ...]
    advice: StructureAdvice

    @property
    def match_rate(self) -> str:
        if not self.latest or not self.latest["downstream_rows"]:
            return "—"
        return f"{self.latest['matched_rows'] / self.latest['downstream_rows']:.2%}"


@dataclass(frozen=True, slots=True)
class RuleDetail:
    id: str
    name: str
    category: str
    condition: str
    limitation: str
    implementation: str
    targets: tuple[str, ...]
    latest: tuple[dict, ...]
    history: tuple[dict, ...]


def _latest_rows(rows: list[dict]) -> list[dict]:
    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (str(row.get("dimension", "")), str(row["check_id"]))
        if key not in latest or row["checked_at"] > latest[key]["checked_at"]:
            latest[key] = row
    return list(latest.values())


def _health_evidence(rows: list[dict]) -> tuple[HealthEvidence, ...]:
    return tuple(
        HealthEvidence(
            HealthDimension(row["dimension"]), HealthStatus(row["status"]),
            row["check_id"], row["evidence_at"], row["expires_at"],
            HealthReason(
                "persisted_result", row["detail"],
                HealthDimension(row["dimension"]), row["check_id"],
            ),
            row["violations"], row["checked_rows"],
        )
        for row in rows
    )


class DataAssetService:
    """把代码内契约与仓库内证据合成页面所需的稳定结构。"""

    def __init__(self, warehouse: Warehouse) -> None:
        self._warehouse = warehouse
        self._query = WarehouseQuery(warehouse)

    def _assessment(self, dataset_id: str, now: datetime | None = None):
        rows = _latest_rows(self._query.dataset_checks(dataset_id).to_dicts())
        return aggregate_health(
            policy_for(dataset_id), _health_evidence(rows), now or datetime.now()
        )

    def tables(self, *, include_raw: bool = False) -> list[DatasetRow]:
        cover = {item.dataset: item for item in coverage(self._warehouse)}
        rows = [
            DatasetRow(item, self._assessment(item.id).status, cover.get(item.id))
            for item in DATASETS.values()
            if include_raw or item.layer is not DatasetLayer.RAW
        ]
        order = {HealthStatus.RED: 0, HealthStatus.YELLOW: 1,
                 HealthStatus.GRAY: 2, HealthStatus.GREEN: 3}
        return sorted(rows, key=lambda row: (order[row.status], row.dataset.layer, row.dataset.id))

    def table(self, dataset_id: str) -> DatasetDetail | None:
        dataset = DATASETS.get(dataset_id)
        if dataset is None:
            return None
        cover = {item.dataset: item for item in coverage(self._warehouse)}
        raw_evidence = self._query.dataset_checks(dataset_id).to_dicts()
        assessment = self._assessment(dataset_id)
        arrow_schema = (schema.ALL | schema.READ_ONLY).get(dataset_id)
        meanings = {
            metric.column: metric.meaning
            for metric in REGISTRY.values() if metric.dataset == dataset_id
        }
        fields = tuple(
            (field.name, str(field.type), meanings.get(field.name, "尚无字段说明"))
            for field in arrow_schema
        ) if arrow_schema else ()
        rules = sorted({
            check_id
            for metric in REGISTRY.values() if metric.dataset == dataset_id
            for check_id in metric.checks
        })
        rules.extend(sorted({row["check_id"] for row in raw_evidence} - set(rules)))
        upstream = tuple(edge for edge in DEPENDENCIES if edge.downstream == dataset_id)
        downstream = tuple(edge for edge in DEPENDENCIES if edge.upstream == dataset_id)
        metrics = [
            metric for metric in REGISTRY.values() if metric.dataset == dataset_id
        ]
        validities = {metric.validity for metric in metrics}
        parameterized = any(
            metric.kind is Kind.FACTOR
            and isinstance(metric.lineage, Computed)
            and bool(metric.lineage.params)
            for metric in metrics
        )
        cadences: list[str] = []
        cadence_complete = True
        for source_id in dataset.sources:
            if source_id in SOURCES and SOURCES[source_id].cadence:
                cadences.append(SOURCES[source_id].cadence or "")
            elif source_id in DATASETS:
                cadences.append(DATASETS[source_id].update_mode)
            else:
                cadence_complete = False
        consumer_boundaries = {
            metric.ext_consumers for metric in metrics if metric.ext_consumers
        }
        suggestion = advise_split(SplitEvidence(
            primary_key_known=bool(dataset.primary_key),
            consumers_known=bool(dataset.consumers),
            health_evidence_complete=(
                bool(assessment.dimensions)
                and all(state is not HealthStatus.GRAY for _, state in assessment.dimensions)
            ),
            mixed_grain=False if dataset.grain != UNKNOWN else None,
            mixed_validity=len(validities) > 1,
            mixed_update_cadence=(len(set(cadences)) > 1)
            if cadence_complete else None,
            fact_and_parameterized_factor=(
                parameterized and any(metric.kind is not Kind.FACTOR for metric in metrics)
            ),
            independent_field_boundary=(
                len(dataset.sources) > 1
                and len(validities) > 1
                and len(consumer_boundaries) > 1
            ),
        ))
        related = tuple(
            (edge, self._pair_advice(edge)) for edge in (*upstream, *downstream)
        )
        return DatasetDetail(
            DatasetRow(dataset, assessment.status, cover.get(dataset_id)),
            domain_label(dataset.domain),
            classify_sources(dataset.sources),
            root_source_refs(dataset_id),
            tuple((_DIMENSION_LABELS[dim], state) for dim, state in assessment.dimensions),
            assessment.reasons, tuple(raw_evidence), upstream, downstream, fields,
            tuple(rules), suggestion, related,
        )

    def _pair_advice(self, edge: DatasetDependency) -> StructureAdvice:
        left, right = DATASETS[edge.upstream], DATASETS[edge.downstream]
        relation_rows = self._query.relation_checks(
            edge.upstream, edge.downstream
        ).to_dicts()
        latest = relation_rows[0] if relation_rows else None
        left_health = self._assessment(left.id).status
        right_health = self._assessment(right.id).status

        def known_equal(first, second):
            if not first or not second or first == UNKNOWN or second == UNKNOWN:
                return None
            return first == second

        complete = None
        if latest is not None:
            complete = (
                edge.cardinality is JoinCardinality.ONE_TO_ONE
                and latest["orphan_rows"] == 0
                and latest["matched_rows"] == latest["downstream_rows"]
                and latest["upstream_rows"] == latest["downstream_rows"]
            )
        left_stage = (
            left.builder.rsplit(".", 1)[0] if "." in left.builder else left.builder
        )
        right_stage = (
            right.builder.rsplit(".", 1)[0] if "." in right.builder else right.builder
        )
        return advise_pair(PairEvidence(
            same_grain=known_equal(left.grain, right.grain),
            same_primary_key=(left.primary_key == right.primary_key)
            if left.primary_key and right.primary_key else None,
            complete_one_to_one=complete,
            same_update_cadence=known_equal(left.update_mode, right.update_mode),
            same_lifecycle=known_equal(left.lifecycle, right.lifecycle),
            same_status=left.status == right.status,
            same_recovery=known_equal(left.update_mode, right.update_mode),
            same_publish_stage=known_equal(left_stage, right_stage),
            independent_arrival=(
                left.sources != right.sources or left.finalized_at != right.finalized_at
            ) if left.sources and right.sources else None,
            independent_consumers=(left.consumers != right.consumers)
            if left.consumers and right.consumers else None,
            upstream_reused=sum(
                dependency.upstream == edge.upstream for dependency in DEPENDENCIES
            ) > 1,
            optional_relation=edge.requirement is DependencyRequirement.OPTIONAL,
            tables_green=(
                left_health is HealthStatus.GREEN
                and right_health is HealthStatus.GREEN
            ),
            relation_green=(latest["status"] == HealthStatus.GREEN.value)
            if latest is not None else None,
        ))

    def relation(self, upstream: str, downstream: str) -> RelationDetail | None:
        dependency = next((
            edge for edge in DEPENDENCIES
            if edge.upstream == upstream and edge.downstream == downstream
        ), None)
        if dependency is None:
            return None
        history = self._query.relation_checks(upstream, downstream).to_dicts()
        return RelationDetail(
            dependency, history[0] if history else None, tuple(history),
            self._pair_advice(dependency),
        )

    def rule(self, check_id: str) -> RuleDetail | None:
        metric_rows = self._query.metric_checks(check_id=check_id).to_dicts()
        dataset_rows = self._query.dataset_checks(check_id=check_id).to_dicts()
        relation_rows = [
            row for row in self._query.relation_checks().to_dicts()
            if row["check_id"] == check_id
        ]
        history = sorted(
            [*metric_rows, *dataset_rows, *relation_rows],
            key=lambda row: row["checked_at"], reverse=True,
        )
        check = CHECKS.get(check_id)
        metadata = _CHECK_METADATA.get(check_id)
        if check is None and metadata is None and not history:
            return None
        if check is not None:
            name = check.description
            category = check.category.value
            condition = "违规数为 0，且适用范围内检查行数大于 0"
            limitation = f"执行范围：{check.scope.value}；已知限制：{', '.join(check.known_issues) or '无登记'}"
            implementation = f"{check.run.__module__}.{check.run.__name__}"
            targets = check.metrics
        elif metadata is not None:
            name, condition, limitation, implementation = metadata
            category = "dataset/relation"
            targets = tuple(sorted({
                str(row.get("dataset_id") or
                    f"{row.get('upstream')}->{row.get('downstream')}")
                for row in history
            }))
        else:
            name = check_id
            category = "persisted"
            condition = "以最近持久化结果的状态或 passed 字段为准"
            limitation = "规则定义未在当前代码注册表中登记"
            implementation = "仅有历史结果"
            targets = ()
        latest = _latest_rows(history)
        return RuleDetail(
            check_id, name, category, condition, limitation, implementation,
            tuple(targets), tuple(latest), tuple(history),
        )
