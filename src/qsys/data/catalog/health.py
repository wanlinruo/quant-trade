"""表级/关系级健康策略、四态门禁与结果持久化。

状态聚合只有一个顺序：RED -> GRAY -> YELLOW -> GREEN。真实性或结构阻断
不能被其他维度的通过抵消；必需证据缺失或过期也不能显示为健康。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
import hashlib
from pathlib import Path

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.catalog.datasets import DATASETS
from qsys.data.catalog.types import (
    Dataset,
    DatasetDependency,
    DependencyRequirement,
    JoinCardinality,
)
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


class HealthStatus(StrEnum):
    RED = "red"
    GRAY = "gray"
    YELLOW = "yellow"
    GREEN = "green"


class HealthDimension(StrEnum):
    FRESHNESS = "freshness"
    COMPLETENESS = "completeness"
    STRUCTURE = "structure"
    VALIDITY = "validity"
    LINEAGE = "lineage"


ALL_DIMENSIONS = tuple(HealthDimension)


@dataclass(frozen=True, slots=True)
class HealthReason:
    code: str
    message: str
    dimension: HealthDimension
    check_id: str | None = None


@dataclass(frozen=True, slots=True)
class HealthEvidence:
    dimension: HealthDimension
    status: HealthStatus
    check_id: str
    observed_at: datetime
    expires_at: datetime | None
    reason: HealthReason
    violations: int = 0
    checked_rows: int = 0


@dataclass(frozen=True, slots=True)
class DatasetHealthPolicy:
    id: str
    required_dimensions: tuple[HealthDimension, ...]
    evidence_ttl: timedelta | None
    allow_empty: bool
    description: str


@dataclass(frozen=True, slots=True)
class HealthAssessment:
    status: HealthStatus
    dimensions: tuple[tuple[HealthDimension, HealthStatus], ...]
    reasons: tuple[HealthReason, ...]
    evidence: tuple[HealthEvidence, ...]


@dataclass(frozen=True, slots=True)
class RelationAssessment:
    evidence: HealthEvidence
    upstream_rows: int
    downstream_rows: int
    matched_rows: int
    orphan_rows: int


_FIVE = ALL_DIMENSIONS
POLICIES: dict[str, DatasetHealthPolicy] = {
    "dense_daily": DatasetHealthPolicy(
        "dense_daily", _FIVE, timedelta(days=3), False,
        "稠密日表必须有交易日、标的和键空间覆盖证据",
    ),
    "minute": DatasetHealthPolicy(
        "minute", _FIVE, timedelta(days=3), False,
        "分钟表检查时刻集合、根数、量价合法性和日线汇总",
    ),
    "snapshot": DatasetHealthPolicy(
        "snapshot", _FIVE, timedelta(days=3), False,
        "点观测快照不得结转成历史区间",
    ),
    "sparse_event": DatasetHealthPolicy(
        "sparse_event", _FIVE, timedelta(days=7), True,
        "事件表以采集成功和事件契约为准，合法零行不失败",
    ),
    "interval": DatasetHealthPolicy(
        "interval", _FIVE, timedelta(days=7), True,
        "区间表检查重叠、断档和有效期",
    ),
    "frozen": DatasetHealthPolicy(
        "frozen", (HealthDimension.STRUCTURE, HealthDimension.VALIDITY), None,
        False, "冻结表检查 Schema、校验和和不可变性，不要求持续更新",
    ),
    "runtime_log": DatasetHealthPolicy(
        "runtime_log",
        (HealthDimension.FRESHNESS, HealthDimension.COMPLETENESS,
         HealthDimension.STRUCTURE, HealthDimension.VALIDITY),
        timedelta(days=3), True,
        "运行日志按服务节奏检查，不套用行情键空间",
    ),
    "realtime": DatasetHealthPolicy(
        "realtime", _FIVE, timedelta(seconds=30), True,
        "实时状态检查行情年龄、序列连续性和服务可用性",
    ),
}


def policy_for(dataset: Dataset | str) -> DatasetHealthPolicy | None:
    item = DATASETS[dataset] if isinstance(dataset, str) else dataset
    return POLICIES.get(item.health_policy) if item.health_policy else None


def aggregate_health(
    policy: DatasetHealthPolicy | None,
    evidence: tuple[HealthEvidence, ...],
    now: datetime,
) -> HealthAssessment:
    """按 RED -> GRAY -> YELLOW -> GREEN 聚合，返回可解释原因。"""
    if policy is None:
        reason = HealthReason(
            "policy_missing", "数据集尚未登记健康策略",
            HealthDimension.STRUCTURE,
        )
        return HealthAssessment(HealthStatus.GRAY, (), (reason,), evidence)

    dimension_states: list[tuple[HealthDimension, HealthStatus]] = []
    reasons: list[HealthReason] = []
    for dimension in policy.required_dimensions:
        candidates = [item for item in evidence if item.dimension is dimension]
        red = [item for item in candidates if item.status is HealthStatus.RED]
        if red:
            state = HealthStatus.RED
            reasons.extend(item.reason for item in red)
        else:
            current = [
                item for item in candidates
                if item.expires_at is None or item.expires_at >= now
            ]
            if not current:
                state = HealthStatus.GRAY
                code = "evidence_expired" if candidates else "evidence_missing"
                message = "必需证据已过期" if candidates else "缺少必需证据"
                reasons.append(HealthReason(code, message, dimension))
            elif any(item.status is HealthStatus.GRAY for item in current):
                state = HealthStatus.GRAY
                reasons.extend(
                    item.reason for item in current
                    if item.status is HealthStatus.GRAY
                )
            elif any(item.status is HealthStatus.YELLOW for item in current):
                state = HealthStatus.YELLOW
                reasons.extend(
                    item.reason for item in current
                    if item.status is HealthStatus.YELLOW
                )
            else:
                state = HealthStatus.GREEN
        dimension_states.append((dimension, state))

    states = {state for _, state in dimension_states}
    if HealthStatus.RED in states:
        overall = HealthStatus.RED
    elif HealthStatus.GRAY in states:
        overall = HealthStatus.GRAY
    elif HealthStatus.YELLOW in states:
        overall = HealthStatus.YELLOW
    else:
        overall = HealthStatus.GREEN
    return HealthAssessment(
        overall, tuple(dimension_states), tuple(reasons), evidence
    )


def _evidence(
    dimension: HealthDimension,
    status: HealthStatus,
    check_id: str,
    observed_at: datetime,
    policy: DatasetHealthPolicy,
    code: str,
    message: str,
    *,
    violations: int = 0,
    checked_rows: int = 0,
) -> HealthEvidence:
    expires = observed_at + policy.evidence_ttl if policy.evidence_ttl else None
    return HealthEvidence(
        dimension, status, check_id, observed_at, expires,
        HealthReason(code, message, dimension, check_id), violations, checked_rows,
    )


def check_table_contract(
    dataset_id: str,
    frame: pl.DataFrame,
    observed_at: datetime,
) -> tuple[HealthEvidence, ...]:
    """检查 Schema、主键非空/唯一及空表语义。

    这是与 I/O 无关的纯检查；时效、字段真实性和血缘由各自 runner 追加证据。
    """
    dataset = DATASETS[dataset_id]
    policy = policy_for(dataset)
    if policy is None:
        return ()
    if not dataset.physical:
        raise ValueError(f"{dataset_id} 是逻辑数据集，没有物理表契约")
    expected = (schema.ALL | schema.READ_ONLY)[dataset_id]
    actual = frame.to_arrow().schema
    schema_problems = []
    if actual.names != expected.names:
        schema_problems.append(
            f"列不一致：实际 {actual.names}，期望 {expected.names}"
        )
    else:
        for field in expected:
            actual_type = actual.field(field.name).type
            compatible_string = (
                (pa.types.is_string(actual_type) or pa.types.is_large_string(actual_type))
                and (pa.types.is_string(field.type) or pa.types.is_large_string(field.type))
            )
            if actual_type != field.type and not compatible_string:
                schema_problems.append(
                    f"{field.name}: 实际 {actual_type}，期望 {field.type}"
                )

    key_problems: list[str] = []
    if not dataset.primary_key:
        key_status = HealthStatus.GRAY
        key_problems.append("主键尚未确认")
        key_violations = 0
    elif not set(dataset.primary_key) <= set(frame.columns):
        key_status = HealthStatus.RED
        key_problems.append("主键列缺失")
        key_violations = 1
    else:
        nulls = sum(frame.get_column(key).null_count() for key in dataset.primary_key)
        unique = frame.n_unique(subset=list(dataset.primary_key))
        duplicates = max(0, frame.height - unique)
        key_violations = nulls + duplicates
        key_status = HealthStatus.RED if key_violations else HealthStatus.GREEN
        if nulls:
            key_problems.append(f"主键 null {nulls} 个")
        if duplicates:
            key_problems.append(f"重复键 {duplicates} 行")

    structure_status = (
        HealthStatus.RED if schema_problems or key_status is HealthStatus.RED
        else HealthStatus.GRAY if key_status is HealthStatus.GRAY
        else HealthStatus.GREEN
    )
    structure_detail = "；".join(schema_problems + key_problems) or "Schema 与主键契约通过"
    structure = _evidence(
        HealthDimension.STRUCTURE, structure_status, "T1.table_contract",
        observed_at, policy,
        "table_contract_failed" if structure_status is HealthStatus.RED
        else "table_contract_unknown" if structure_status is HealthStatus.GRAY
        else "table_contract_ok",
        structure_detail,
        violations=len(schema_problems) + key_violations,
        checked_rows=frame.height,
    )

    if frame.height:
        completeness_status = HealthStatus.GREEN
        code = "rows_present"
        detail = f"检查 {frame.height} 行"
    elif policy.allow_empty:
        completeness_status = HealthStatus.GREEN
        code = "empty_allowed"
        detail = "该类数据集允许合法零行"
    else:
        completeness_status = HealthStatus.RED
        code = "unexpected_empty"
        detail = "需要数据但检查范围为零行"
    completeness = _evidence(
        HealthDimension.COMPLETENESS, completeness_status,
        "T2.table_nonempty", observed_at, policy, code, detail,
        violations=int(completeness_status is HealthStatus.RED),
        checked_rows=frame.height,
    )
    return structure, completeness


def freshness_evidence(
    dataset_id: str,
    published_at: datetime | None,
    now: datetime,
) -> HealthEvidence:
    """把最后发布时间转成时效证据；冻结表不要求持续刷新。"""
    policy = policy_for(dataset_id)
    if policy is None:
        raise ValueError(f"{dataset_id} 没有健康策略")
    if HealthDimension.FRESHNESS not in policy.required_dimensions:
        return _evidence(
            HealthDimension.FRESHNESS, HealthStatus.GREEN, "T3.freshness",
            now, policy, "freshness_not_required", "该类数据集不要求持续刷新",
        )
    if published_at is None:
        return _evidence(
            HealthDimension.FRESHNESS, HealthStatus.GRAY, "T3.freshness",
            now, policy, "publish_time_missing", "缺少最后发布时间",
        )
    expires = published_at + (policy.evidence_ttl or timedelta(0))
    status = HealthStatus.RED if expires < now else HealthStatus.GREEN
    return HealthEvidence(
        HealthDimension.FRESHNESS, status, "T3.freshness", published_at,
        expires,
        HealthReason(
            "freshness_stale" if status is HealthStatus.RED else "freshness_ok",
            f"最后发布于 {published_at.isoformat()}",
            HealthDimension.FRESHNESS, "T3.freshness",
        ),
        violations=int(status is HealthStatus.RED), checked_rows=1,
    )


def field_status_evidence(
    dataset_id: str,
    statuses: tuple[HealthStatus, ...],
    observed_at: datetime,
) -> HealthEvidence:
    """把字段级检查汇总为表级真实性证据，不用平均分稀释失败。"""
    policy = policy_for(dataset_id)
    if policy is None:
        raise ValueError(f"{dataset_id} 没有健康策略")
    if not statuses or HealthStatus.GRAY in statuses:
        status = HealthStatus.GRAY
        code = "field_evidence_missing"
    elif HealthStatus.RED in statuses:
        status = HealthStatus.RED
        code = "field_check_blocking"
    elif HealthStatus.YELLOW in statuses:
        status = HealthStatus.YELLOW
        code = "field_check_warning"
    else:
        status = HealthStatus.GREEN
        code = "field_checks_ok"
    return _evidence(
        HealthDimension.VALIDITY, status, "T4.field_status", observed_at,
        policy, code, f"汇总 {len(statuses)} 条字段状态",
        violations=sum(item is HealthStatus.RED for item in statuses),
        checked_rows=len(statuses),
    )


def frozen_checksum_evidence(
    dataset_id: str,
    files: tuple[Path, ...],
    expected: dict[str, str] | None,
    observed_at: datetime,
    *,
    root: Path | None = None,
) -> HealthEvidence:
    """冻结表不可变性检查；没有基线时保持灰色，绝不猜测健康。"""
    policy = policy_for(dataset_id)
    if policy is None:
        raise ValueError(f"{dataset_id} 没有健康策略")
    if policy.id != "frozen":
        raise ValueError(f"{dataset_id} 不是冻结数据集")
    root = Path(root) if root is not None else None

    def label(path: Path) -> str:
        if root is not None:
            try:
                return str(path.relative_to(root))
            except ValueError:
                pass
        return str(path)

    actual = {
        label(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
    }
    if expected is None:
        status = HealthStatus.GRAY
        code = "checksum_baseline_missing"
        detail = f"扫描 {len(actual)} 个文件，但没有冻结校验和基线"
        violations = 0
    else:
        changed = sorted(
            key for key in set(actual) | set(expected)
            if actual.get(key) != expected.get(key)
        )
        status = HealthStatus.RED if changed else HealthStatus.GREEN
        code = "frozen_content_changed" if changed else "frozen_content_unchanged"
        detail = f"校验 {len(actual)} 个文件；变化 {changed}"
        violations = len(changed)
    return _evidence(
        HealthDimension.VALIDITY, status, "T5.frozen_checksum",
        observed_at, policy, code, detail,
        violations=violations, checked_rows=len(actual),
    )


def partition_layout_evidence(
    dataset_id: str,
    files: tuple[Path, ...],
    observed_at: datetime,
) -> HealthEvidence:
    """检查物理分区目录是否符合表契约，不打开数据文件。"""
    dataset = DATASETS[dataset_id]
    policy = policy_for(dataset)
    if policy is None:
        raise ValueError(f"{dataset_id} 没有健康策略")
    if not files:
        return _evidence(
            HealthDimension.STRUCTURE, HealthStatus.GRAY, "T6.partition_layout",
            observed_at, policy, "partition_evidence_missing", "没有可检查的文件路径",
        )
    problems: list[str] = []
    for path in files:
        has_year = any(part.startswith("year=") for part in path.parts)
        if dataset.partition_keys and not has_year:
            problems.append(f"{path}: 缺 year= 分区")
        if not dataset.partition_keys and has_year:
            problems.append(f"{path}: 契约声明不分区却出现 year= 目录")
    status = HealthStatus.RED if problems else HealthStatus.GREEN
    return _evidence(
        HealthDimension.STRUCTURE, status, "T6.partition_layout",
        observed_at, policy,
        "partition_layout_failed" if problems else "partition_layout_ok",
        "；".join(problems) if problems else f"{len(files)} 个路径符合分区契约",
        violations=len(problems), checked_rows=len(files),
    )


def relation_recompute_evidence(
    dependency: DatasetDependency,
    metric_statuses: dict[str, HealthStatus],
    observed_at: datetime,
) -> HealthEvidence:
    """要求关系产出的每个字段都有独立重算证据。"""
    policy = DatasetHealthPolicy(
        "relation", (HealthDimension.LINEAGE,), timedelta(days=3),
        dependency.requirement is DependencyRequirement.OPTIONAL,
        "关系证据",
    )
    missing = sorted(set(dependency.metrics) - set(metric_statuses))
    statuses = tuple(metric_statuses[metric] for metric in dependency.metrics if metric in metric_statuses)
    if missing or HealthStatus.GRAY in statuses or not dependency.metrics:
        status = HealthStatus.GRAY
        code = "recompute_evidence_missing"
    elif HealthStatus.RED in statuses:
        status = HealthStatus.RED
        code = "recompute_failed"
    elif HealthStatus.YELLOW in statuses:
        status = HealthStatus.YELLOW
        code = "recompute_warning"
    else:
        status = HealthStatus.GREEN
        code = "recompute_ok"
    return _evidence(
        HealthDimension.LINEAGE, status, "R2.independent_recompute",
        observed_at, policy, code,
        f"产出指标 {len(dependency.metrics)} 个；缺证据 {missing}",
        violations=sum(item is HealthStatus.RED for item in statuses),
        checked_rows=len(statuses),
    )


def _duplicate_excess(frame: pl.DataFrame, keys: list[str]) -> int:
    return max(0, frame.height - frame.n_unique(subset=keys))


def check_dependency(
    dependency: DatasetDependency,
    upstream: pl.DataFrame,
    downstream: pl.DataFrame,
    observed_at: datetime,
    *,
    upstream_published_at: datetime | None = None,
    downstream_published_at: datetime | None = None,
) -> RelationAssessment:
    """检查 Join 键、基数、孤儿行和上下游刷新顺序。"""
    policy = DatasetHealthPolicy(
        "relation", (HealthDimension.LINEAGE,), timedelta(days=3),
        dependency.requirement is DependencyRequirement.OPTIONAL,
        "关系证据",
    )
    if not dependency.join_keys or dependency.cardinality is JoinCardinality.UNKNOWN:
        evidence = _evidence(
            HealthDimension.LINEAGE, HealthStatus.GRAY, "R1.relation_contract",
            observed_at, policy, "relation_metadata_missing",
            "Join 键或基数尚未确认",
            checked_rows=downstream.height,
        )
        return RelationAssessment(evidence, upstream.height, downstream.height, 0, 0)

    up_keys = [left for left, _ in dependency.join_keys]
    down_keys = [right for _, right in dependency.join_keys]
    if not set(up_keys) <= set(upstream.columns) or not set(down_keys) <= set(downstream.columns):
        evidence = _evidence(
            HealthDimension.LINEAGE, HealthStatus.RED, "R1.relation_contract",
            observed_at, policy, "join_key_missing", "关系所需 Join 键不存在",
            violations=1, checked_rows=downstream.height,
        )
        return RelationAssessment(evidence, upstream.height, downstream.height, 0, downstream.height)

    up_nulls = sum(upstream.get_column(key).null_count() for key in up_keys)
    down_nulls = sum(downstream.get_column(key).null_count() for key in down_keys)
    up_duplicates = _duplicate_excess(upstream, up_keys)
    down_duplicates = _duplicate_excess(downstream, down_keys)
    cardinality_violations = 0
    if dependency.cardinality in (JoinCardinality.ONE_TO_ONE, JoinCardinality.ONE_TO_MANY):
        cardinality_violations += up_duplicates
    if dependency.cardinality in (JoinCardinality.ONE_TO_ONE, JoinCardinality.MANY_TO_ONE):
        cardinality_violations += down_duplicates

    lookup = upstream.select([
        pl.col(left).alias(right) for left, right in dependency.join_keys
    ]).unique().with_columns(pl.lit(True).alias("__matched"))
    joined = downstream.join(lookup, on=down_keys, how="left")
    matched_rows = joined.filter(pl.col("__matched").is_not_null()).height
    orphan_rows = downstream.height - matched_rows
    stale = (
        upstream_published_at is not None
        and (downstream_published_at is None or downstream_published_at < upstream_published_at)
    )
    violations = up_nulls + down_nulls + cardinality_violations + orphan_rows + int(stale)
    if up_nulls or down_nulls or cardinality_violations or stale:
        status = HealthStatus.RED
    elif orphan_rows and dependency.requirement is DependencyRequirement.REQUIRED:
        status = HealthStatus.RED
    elif orphan_rows:
        status = HealthStatus.YELLOW
    else:
        status = HealthStatus.GREEN
    detail = (
        f"keys={dependency.join_keys}; cardinality={dependency.cardinality.value}; "
        f"upstream={upstream.height}; downstream={downstream.height}; "
        f"matched={matched_rows}; orphan={orphan_rows}; "
        f"duplicate_violations={cardinality_violations}; null_keys={up_nulls + down_nulls}; "
        f"downstream_stale={stale}"
    )
    evidence = _evidence(
        HealthDimension.LINEAGE, status, "R1.join_integrity", observed_at,
        policy,
        "relation_failed" if status is HealthStatus.RED
        else "relation_warning" if status is HealthStatus.YELLOW
        else "relation_ok",
        detail, violations=violations, checked_rows=downstream.height,
    )
    return RelationAssessment(
        evidence, upstream.height, downstream.height, matched_rows, orphan_rows
    )


def dataset_check_frame(
    run_id: str,
    checked_at: datetime,
    dataset_id: str,
    evidence: tuple[HealthEvidence, ...],
    *,
    scope_start: date | None = None,
    scope_end: date | None = None,
) -> pl.DataFrame:
    policy = policy_for(dataset_id)
    required = set(policy.required_dimensions) if policy else set()
    rows = [{
        "run_id": run_id, "checked_at": checked_at, "dataset_id": dataset_id,
        "check_id": item.check_id, "dimension": item.dimension.value,
        "scope_start": scope_start, "scope_end": scope_end,
        "status": item.status.value, "evidence_at": item.observed_at,
        "expires_at": item.expires_at, "required": item.dimension in required,
        "violations": item.violations, "checked_rows": item.checked_rows,
        "detail": item.reason.message,
    } for item in evidence]
    return _rows(rows, schema.DATASET_CHECKS)


def relation_check_frame(
    run_id: str,
    checked_at: datetime,
    dependency: DatasetDependency,
    result: RelationAssessment,
    *,
    scope_start: date | None = None,
    scope_end: date | None = None,
) -> pl.DataFrame:
    item = result.evidence
    row = {
        "run_id": run_id, "checked_at": checked_at,
        "upstream": dependency.upstream, "downstream": dependency.downstream,
        "check_id": item.check_id, "dimension": item.dimension.value,
        "scope_start": scope_start, "scope_end": scope_end,
        "status": item.status.value, "evidence_at": item.observed_at,
        "expires_at": item.expires_at,
        "required": dependency.requirement is DependencyRequirement.REQUIRED,
        "upstream_rows": result.upstream_rows, "downstream_rows": result.downstream_rows,
        "matched_rows": result.matched_rows, "orphan_rows": result.orphan_rows,
        "violations": item.violations, "detail": item.reason.message,
    }
    return _rows([row], schema.RELATION_CHECKS)


def _rows(rows: list[dict[str, object]], target: pa.Schema) -> pl.DataFrame:
    if not rows:
        return pl.from_arrow(target.empty_table())
    return pl.from_arrow(
        pl.DataFrame(rows).select(target.names).to_arrow().cast(target)
    )


def _append_results(
    warehouse: Warehouse,
    dataset: str,
    frame: pl.DataFrame,
    target: pa.Schema,
) -> int:
    if frame.height == 0:
        return 0
    years = sorted(set(frame["checked_at"].dt.year().to_list()))
    query = WarehouseQuery(warehouse)
    reader = getattr(query, dataset)
    written = 0
    for year in years:
        chunk = frame.filter(pl.col("checked_at").dt.year() == year)
        existing = reader().filter(pl.col("checked_at").dt.year() == year)
        merged = pl.concat([existing, chunk], how="vertical")
        warehouse.write(
            dataset,
            merged.select(target.names).to_arrow().cast(target),
            year=year,
        )
        written += chunk.height
    return written


def write_dataset_checks(warehouse: Warehouse, frame: pl.DataFrame) -> int:
    return _append_results(warehouse, "dataset_checks", frame, schema.DATASET_CHECKS)


def write_relation_checks(warehouse: Warehouse, frame: pl.DataFrame) -> int:
    return _append_results(warehouse, "relation_checks", frame, schema.RELATION_CHECKS)
