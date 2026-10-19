from datetime import datetime

import polars as pl

from qsys.data.catalog.health import HealthStatus
from qsys.data.cutover_health import (
    _check_canonical_relation,
    _combine_evidence,
    _reconciliation_evidence,
    canonical_required_relations,
    generate_cutover_relations,
)
from qsys.data.query import WarehouseQuery
from qsys.data.pipeline.canonical_reference import Reconciliation


NOW = datetime(2026, 9, 20, 20)


def test_reconciliation_supplies_validity_and_lineage_without_hiding_warning():
    result = Reconciliation(10, 10, (), (), ())
    validity, lineage = _reconciliation_evidence(
        "security_name_history", result, NOW,
        validity_warning="存在 2 个区间问题",
    )

    assert validity.status is HealthStatus.YELLOW
    assert lineage.status is HealthStatus.GREEN
    assert "2 个区间问题" in validity.reason.message


def test_reconciliation_failure_is_red_for_validity_and_lineage():
    result = Reconciliation(10, 9, (("A",),), (), ())

    evidence = _reconciliation_evidence("bar_daily", result, NOW)

    assert {item.status for item in evidence} == {HealthStatus.RED}
    assert all(item.violations == 1 for item in evidence)


def test_combined_partition_evidence_keeps_worst_status():
    green = _reconciliation_evidence(
        "bar_minute_1m", Reconciliation(10, 10, (), (), ()), NOW
    )[0]
    red = _reconciliation_evidence(
        "bar_minute_1m", Reconciliation(10, 9, (("A",),), (), ()), NOW
    )[0]

    combined = _combine_evidence([green, red])

    assert combined.status is HealthStatus.RED
    assert combined.checked_rows == 19
    assert combined.violations == 1


def test_required_canonical_relations_are_generated_and_persisted(warehouse):
    relations = canonical_required_relations()

    summary = generate_cutover_relations(
        warehouse, NOW.date(), NOW.date(), now=NOW
    )

    assert len(relations) == 7
    assert set(summary.values()) == {HealthStatus.GREEN}
    stored = WarehouseQuery(warehouse).relation_checks()
    assert stored.height == len(relations)
    assert set(stored["status"].to_list()) == {HealthStatus.GREEN.value}


def test_bar_status_relation_allows_suspended_status_without_bar():
    dependency = next(
        edge for edge in canonical_required_relations()
        if (edge.upstream, edge.downstream)
        == ("bar_daily", "security_status_daily")
    )
    bars = pl.DataFrame({"symbol": ["A"], "dt": [NOW.date()]})
    statuses = pl.DataFrame({
        "symbol": ["A", "SUSPENDED"], "dt": [NOW.date(), NOW.date()]
    })

    good = _check_canonical_relation(dependency, bars, statuses, NOW)
    missing = _check_canonical_relation(
        dependency,
        pl.DataFrame({"symbol": ["A", "B"], "dt": [NOW.date(), NOW.date()]}),
        statuses,
        NOW,
    )

    assert good.evidence.status is HealthStatus.GREEN
    assert missing.evidence.status is HealthStatus.RED
    assert missing.orphan_rows == 1


def test_minute_streaming_reconciliation_counts_missing_and_null_mismatches(tmp_path):
    from qsys.data.cutover_health import _minute_reconciliation
    from qsys.data.query_budget import query_budget
    stamps = [NOW, NOW, NOW]
    source = pl.DataFrame({'symbol': ['A', 'B', 'C'], 'ts': stamps, 'close': [1., None, 3.]})
    target = pl.DataFrame({'symbol': ['B', 'C', 'D'], 'ts': stamps, 'close': [2., 3., 4.]})
    with query_budget(memory_limit_mb=64, temp_root=tmp_path,
                      max_temp_directory_mb=64, threads=1):
        result = _minute_reconciliation(source, target)
    assert result.left_rows == result.right_rows == 3
    assert (result.left_only_count, result.right_only_count, result.mismatch_count) == (1, 1, 1)
    evidence = _reconciliation_evidence('bar_minute_1m', result, NOW)
    assert all(item.status is HealthStatus.RED and item.violations == 3 for item in evidence)


def test_daily_health_persists_actual_scope_and_keeps_missing_data_red(warehouse):
    from qsys.data import schema
    from qsys.data.cutover_health import generate_cutover_health
    from qsys.data.pipeline.canonical_reference import normalize_trading_calendar
    from qsys.data.cutover import CANONICAL_CUTOVER_DATASETS
    raw = pl.DataFrame({'cal_date': [NOW.strftime('%Y%m%d')], 'is_open': [1],
                        'pretrade_date': ['20260917']})
    frame = normalize_trading_calendar(raw)
    warehouse.write('trading_calendar', frame.to_arrow().cast(schema.TRADING_CALENDAR), year=NOW.year)
    summary = generate_cutover_health(warehouse, NOW.date(), NOW.date(), raw, now=NOW)
    assert set(summary) == set(CANONICAL_CUTOVER_DATASETS)
    assert summary['bar_daily'] is HealthStatus.RED
    assert summary['bar_minute_1m'] is HealthStatus.RED
    rows = WarehouseQuery(warehouse).dataset_checks()
    assert rows['scope_start'].unique().to_list() == [NOW.date()]
    assert rows['scope_end'].unique().to_list() == [NOW.date()]
    assert rows['dataset_id'].n_unique() == 12
    assert WarehouseQuery(warehouse).relation_checks().height == 7
