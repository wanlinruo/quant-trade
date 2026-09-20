from datetime import date, datetime, timedelta
import hashlib

import polars as pl

from qsys.data import schema
from qsys.data.catalog import DATASETS, DEPENDENCIES
from qsys.data.catalog.health import (
    ALL_DIMENSIONS,
    HealthDimension,
    HealthEvidence,
    HealthReason,
    HealthStatus,
    POLICIES,
    aggregate_health,
    check_dependency,
    check_table_contract,
    dataset_check_frame,
    field_status_evidence,
    freshness_evidence,
    frozen_checksum_evidence,
    partition_layout_evidence,
    relation_recompute_evidence,
    relation_check_frame,
    write_dataset_checks,
    write_relation_checks,
)
from qsys.data.catalog.types import (
    DatasetDependency,
    DependencyKind,
    DependencyRequirement,
    JoinCardinality,
)
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


NOW = datetime(2026, 9, 20, 20)
DAY = date(2026, 9, 20)


def _item(
    dimension: HealthDimension,
    status: HealthStatus = HealthStatus.GREEN,
    *,
    expires_at: datetime | None = None,
) -> HealthEvidence:
    return HealthEvidence(
        dimension, status, f"test.{dimension.value}", NOW, expires_at,
        HealthReason("test", status.value, dimension, f"test.{dimension.value}"),
        violations=int(status is HealthStatus.RED), checked_rows=10,
    )


def _complete(statuses: dict[HealthDimension, HealthStatus] | None = None):
    statuses = statuses or {}
    return tuple(_item(dim, statuses.get(dim, HealthStatus.GREEN)) for dim in ALL_DIMENSIONS)


def test_blocking_truth_failure_is_red_even_when_coverage_is_green():
    evidence = _complete({HealthDimension.VALIDITY: HealthStatus.RED})
    got = aggregate_health(POLICIES["dense_daily"], evidence, NOW)
    assert got.status is HealthStatus.RED


def test_missing_or_expired_required_evidence_is_gray():
    incomplete = tuple(
        _item(dim) for dim in ALL_DIMENSIONS if dim is not HealthDimension.LINEAGE
    )
    assert aggregate_health(POLICIES["dense_daily"], incomplete, NOW).status is HealthStatus.GRAY
    expired = _complete()[:-1] + (
        _item(HealthDimension.LINEAGE, expires_at=NOW - timedelta(seconds=1)),
    )
    assert aggregate_health(POLICIES["dense_daily"], expired, NOW).status is HealthStatus.GRAY


def test_known_defect_stays_yellow_not_green():
    evidence = _complete({HealthDimension.VALIDITY: HealthStatus.YELLOW})
    got = aggregate_health(POLICIES["dense_daily"], evidence, NOW)
    assert got.status is HealthStatus.YELLOW
    assert got.evidence == evidence


def test_frozen_policy_does_not_require_freshness():
    evidence = (
        _item(HealthDimension.STRUCTURE),
        _item(HealthDimension.VALIDITY),
    )
    got = aggregate_health(POLICIES["frozen"], evidence, NOW + timedelta(days=3650))
    assert got.status is HealthStatus.GREEN


def test_frozen_checksum_change_is_red(tmp_path):
    path = tmp_path / "paid_daily" / "part.parquet"
    path.parent.mkdir()
    path.write_bytes(b"before")
    baseline = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(b"before").hexdigest()
    }
    path.write_bytes(b"after")
    got = frozen_checksum_evidence(
        "paid_daily", (path,), baseline, NOW, root=tmp_path
    )
    assert got.status is HealthStatus.RED
    assert got.violations == 1


def test_frozen_checksum_without_baseline_is_gray(tmp_path):
    path = tmp_path / "part.parquet"
    path.write_bytes(b"evidence")
    got = frozen_checksum_evidence("paid_daily", (path,), None, NOW)
    assert got.status is HealthStatus.GRAY


def test_field_red_is_not_diluted_by_green_fields():
    got = field_status_evidence(
        "tushare_daily",
        (HealthStatus.GREEN, HealthStatus.GREEN, HealthStatus.RED), NOW,
    )
    assert got.status is HealthStatus.RED


def test_missing_publish_time_is_gray_and_stale_publish_is_red():
    assert freshness_evidence("tushare_daily", None, NOW).status is HealthStatus.GRAY
    stale = freshness_evidence(
        "tushare_daily", NOW - timedelta(days=4), NOW
    )
    assert stale.status is HealthStatus.RED


def test_partition_layout_mismatch_is_red(tmp_path):
    wrong = tmp_path / "tushare_daily" / "part.parquet"
    got = partition_layout_evidence("tushare_daily", (wrong,), NOW)
    assert got.status is HealthStatus.RED
    right = tmp_path / "tushare_daily" / "year=2026" / "part.parquet"
    assert partition_layout_evidence(
        "tushare_daily", (right,), NOW
    ).status is HealthStatus.GREEN


def test_sparse_event_table_may_legitimately_have_zero_rows():
    frame = pl.from_arrow(schema.SUSPEND_DAILY.empty_table())
    evidence = check_table_contract("suspend_daily", frame, NOW)
    completeness = next(x for x in evidence if x.dimension is HealthDimension.COMPLETENESS)
    assert completeness.status is HealthStatus.GREEN
    assert completeness.checked_rows == 0


def test_dense_table_zero_rows_is_red():
    frame = pl.from_arrow(schema.TUSHARE_DAILY.empty_table())
    evidence = check_table_contract("tushare_daily", frame, NOW)
    completeness = next(x for x in evidence if x.dimension is HealthDimension.COMPLETENESS)
    assert completeness.status is HealthStatus.RED


def test_duplicate_or_null_primary_keys_are_red():
    frame = pl.from_arrow(schema.PRICE_LIMITS_DAILY.empty_table())
    row = {
        "symbol": "000001.SZ", "dt": DAY, "pre_close": 10.0,
        "limit_up": 11.0, "limit_down": 9.0,
    }
    frame = pl.concat([frame, pl.DataFrame([row, row]).cast(frame.schema)])
    evidence = check_table_contract("price_limits_daily", frame, NOW)
    structure = next(x for x in evidence if x.dimension is HealthDimension.STRUCTURE)
    assert structure.status is HealthStatus.RED
    assert structure.violations == 1


def _dependency(requirement=DependencyRequirement.REQUIRED, cardinality=JoinCardinality.ONE_TO_ONE):
    return DatasetDependency(
        "up", "down", DependencyKind.JOIN, (("symbol", "symbol"),), cardinality,
        requirement, "同批次", (), "lineage_freshness",
    )


def test_relation_catches_wrong_cardinality_and_orphans():
    upstream = pl.DataFrame({"symbol": ["A", "A"]})
    downstream = pl.DataFrame({"symbol": ["A", "B"]})
    got = check_dependency(_dependency(), upstream, downstream, NOW)
    assert got.evidence.status is HealthStatus.RED
    assert got.orphan_rows == 1
    assert got.evidence.violations == 2  # 一行重复 + 一个孤儿


def test_optional_orphans_are_yellow():
    upstream = pl.DataFrame({"symbol": ["A"]})
    downstream = pl.DataFrame({"symbol": ["A", "B"]})
    got = check_dependency(
        _dependency(DependencyRequirement.OPTIONAL, JoinCardinality.ONE_TO_MANY),
        upstream, downstream, NOW,
    )
    assert got.evidence.status is HealthStatus.YELLOW


def test_upstream_newer_than_downstream_is_red():
    frame = pl.DataFrame({"symbol": ["A"]})
    got = check_dependency(
        _dependency(), frame, frame, NOW,
        upstream_published_at=NOW,
        downstream_published_at=NOW - timedelta(minutes=1),
    )
    assert got.evidence.status is HealthStatus.RED
    assert "downstream_stale=True" in got.evidence.reason.message


def test_unknown_join_metadata_is_gray():
    dependency = DatasetDependency(
        "up", "down", DependencyKind.JOIN, (), JoinCardinality.UNKNOWN,
        DependencyRequirement.REQUIRED, "待确认", (), None,
    )
    got = check_dependency(dependency, pl.DataFrame(), pl.DataFrame(), NOW)
    assert got.evidence.status is HealthStatus.GRAY


def test_relation_requires_independent_recompute_evidence():
    dependency = next(
        edge for edge in DEPENDENCIES
        if edge.upstream == "price_limits_daily" and edge.downstream == "daily_flags"
    )
    assert dependency.metrics
    assert relation_recompute_evidence(
        dependency, {}, NOW
    ).status is HealthStatus.GRAY
    statuses = {metric: HealthStatus.GREEN for metric in dependency.metrics}
    assert relation_recompute_evidence(
        dependency, statuses, NOW
    ).status is HealthStatus.GREEN
    statuses[dependency.metrics[0]] = HealthStatus.RED
    assert relation_recompute_evidence(
        dependency, statuses, NOW
    ).status is HealthStatus.RED


def test_health_results_round_trip_in_separate_tables(warehouse: Warehouse):
    table_evidence = check_table_contract(
        "suspend_daily", pl.from_arrow(schema.SUSPEND_DAILY.empty_table()), NOW
    )
    table_frame = dataset_check_frame(
        "r1", NOW, "suspend_daily", table_evidence,
        scope_start=DAY, scope_end=DAY,
    )
    dependency = next(
        edge for edge in DEPENDENCIES
        if edge.upstream == "price_limits_daily" and edge.downstream == "daily_flags"
    )
    keys = pl.DataFrame({"symbol": ["A"], "dt": [DAY]})
    relation = check_dependency(dependency, keys, keys, NOW)
    relation_frame = relation_check_frame(
        "r1", NOW, dependency, relation, scope_start=DAY, scope_end=DAY
    )

    assert write_dataset_checks(warehouse, table_frame) == 2
    assert write_relation_checks(warehouse, relation_frame) == 1
    query = WarehouseQuery(warehouse)
    assert query.dataset_checks("suspend_daily").height == 2
    assert query.relation_checks("price_limits_daily", "daily_flags").height == 1
    assert query.relation_checks()["matched_rows"][0] == 1


def test_every_registered_health_policy_resolves():
    assert all(
        item.health_policy in POLICIES
        for item in DATASETS.values()
        if item.health_policy is not None
    )
