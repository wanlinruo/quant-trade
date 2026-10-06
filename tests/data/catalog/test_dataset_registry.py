"""表级契约和依赖图的守护测试。"""

from collections import Counter

from qsys.data import schema
from qsys.data.catalog import DATASETS, DEPENDENCIES, REGISTRY, lineage_edges
from qsys.data.catalog.types import DataDomain, DatasetLayer, DatasetStatus, UNKNOWN, Unclassified


def test_every_physical_dataset_is_registered_exactly_once():
    expected = set(schema.ALL) | set(schema.READ_ONLY)
    physical = {ident for ident, item in DATASETS.items() if item.physical}
    assert physical == expected
    assert all(DATASETS[ident].schema_id == ident for ident in expected)


def test_every_unlanded_metric_dataset_has_a_logical_contract():
    logical_metric_datasets = {m.dataset for m in REGISTRY.values() if not m.landed}
    assert logical_metric_datasets <= set(DATASETS)
    assert all(not DATASETS[ident].physical for ident in logical_metric_datasets)


def test_keys_and_time_columns_exist_in_physical_schema():
    schemas = schema.ALL | schema.READ_ONLY
    for item in DATASETS.values():
        if not item.physical:
            continue
        columns = set(schemas[item.schema_id].names)
        assert set(item.primary_key) <= columns, item.id
        assert set(item.time_columns) <= columns, item.id
        assert set(item.sort_keys) <= columns, item.id


def test_every_inferred_cross_dataset_lineage_has_one_dependency():
    inferred = lineage_edges(REGISTRY)
    declared = [(edge.upstream, edge.downstream) for edge in DEPENDENCIES]
    assert len(declared) == len(set(declared))
    assert set(inferred) <= set(declared)
    by_edge = {(edge.upstream, edge.downstream): edge for edge in DEPENDENCIES}
    for pair, metrics in inferred.items():
        assert by_edge[pair].metrics == metrics


def test_dependencies_resolve_and_are_acyclic():
    graph: dict[str, set[str]] = {ident: set() for ident in DATASETS}
    for edge in DEPENDENCIES:
        assert edge.upstream in DATASETS
        assert edge.downstream in DATASETS
        graph[edge.upstream].add(edge.downstream)

    def visit(node: str, active: frozenset[str], done: set[str]) -> None:
        assert node not in active, f"数据集依赖成环：{sorted(active | {node})}"
        if node in done:
            return
        for downstream in graph[node]:
            visit(downstream, active | {node}, done)
        done.add(node)

    completed: set[str] = set()
    for dataset in graph:
        visit(dataset, frozenset(), completed)


def test_raw_datasets_do_not_claim_production_strategy_consumers():
    forbidden = {"策略", "策略与回测", "结构策略", "实盘核心"}
    for item in DATASETS.values():
        if item.layer is DatasetLayer.RAW:
            assert not (set(item.consumers) & forbidden), item.id


def test_unknown_facts_are_explicit_not_blank():
    for item in DATASETS.values():
        assert item.grain
        assert item.builder
        assert item.update_mode
        assert item.finalized_at
        assert item.lifecycle
    assert DATASETS["ingest_log"].primary_key == ()
    assert UNKNOWN in DATASETS["ingest_log"].grain


def test_read_only_evidence_is_frozen_and_not_writable():
    for ident in schema.READ_ONLY:
        item = DATASETS[ident]
        assert item.status is DatasetStatus.FROZEN
    assert DATASETS["paid_daily"].layer is DatasetLayer.RAW


def test_d18_retired_datasets_do_not_claim_active_daily_arrival():
    snapshots = DATASETS["name_snapshots"]
    assert snapshots.status is DatasetStatus.FROZEN
    assert snapshots.health_policy == "frozen"
    assert "不再要求每日到货" in snapshots.lifecycle

    ingest = DATASETS["ingest_log"]
    assert "每日服务管理不读取" in ingest.lifecycle
    assert "运维检查" not in ingest.consumers


def test_d16_qmt_capital_is_frozen_and_canonical_uses_tushare_daily():
    legacy = DATASETS["share_capital"]
    assert legacy.status is DatasetStatus.FROZEN
    assert legacy.health_policy == "frozen"
    assert "退出每日归档" in legacy.lifecycle

    canonical = DATASETS["share_capital_history"]
    assert canonical.sources == ("tushare_daily",)
    assert "announcement_date 恒 null" in canonical.lifecycle


def test_every_dataset_declares_a_domain():
    """新表不标 domain 就建不起来；Unclassified 必须带理由，不能是空字符串。"""
    for ident, item in DATASETS.items():
        assert isinstance(item.domain, (DataDomain, Unclassified)), ident
        if isinstance(item.domain, Unclassified):
            assert item.domain.reason.strip(), f"{ident}: UNCLASSIFIED 必须说明理由"


def test_domain_distribution_matches_the_spec():
    """数目对不上说明有表漏标或归错——spec §4.2 的表是这份测试的唯一依据。"""
    counts = Counter(
        item.domain.value for item in DATASETS.values() if isinstance(item.domain, DataDomain)
    )
    assert counts == {
        "reference": 11, "market": 18, "fundamental": 4, "factor": 6, "runtime": 10,
    }
    assert sum(counts.values()) == len(DATASETS) == 49


def test_empty_domains_have_no_datasets():
    """宏观与另类当前为空，若将来接入，这条测试会提醒更新 spec 与页面说明。"""
    present = {item.domain for item in DATASETS.values() if isinstance(item.domain, DataDomain)}
    assert DataDomain.MACRO not in present
    assert DataDomain.ALTERNATIVE not in present
