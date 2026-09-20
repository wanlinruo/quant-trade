"""表级契约和依赖图的守护测试。"""

from qsys.data import schema
from qsys.data.catalog import DATASETS, DEPENDENCIES, REGISTRY, lineage_edges
from qsys.data.catalog.types import DatasetLayer, DatasetStatus, UNKNOWN


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
