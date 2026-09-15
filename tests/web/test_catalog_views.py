"""数据层总览的状态折叠、来源展示、排序与筛选。"""

from datetime import date, datetime

import polars as pl

from qsys.data import schema
from qsys.web.catalog_views import CatalogService, Status

D = date(2026, 9, 15)
T = datetime(2026, 9, 15, 19, 30)


def _check_rows(warehouse, rows):
    base = {
        "run_id": "r1", "checked_at": T, "category": "truthfulness",
        "scope_start": D, "scope_end": D, "severity": "blocking",
        "known_issue": None, "violations": 0, "checked_rows": 100,
        "detail": "", "sample_path": None,
    }
    frame = pl.DataFrame([{**base, **row} for row in rows])
    warehouse.write(
        "metric_checks",
        frame.select(schema.METRIC_CHECKS.names).to_arrow().cast(schema.METRIC_CHECKS),
        year=2026,
    )


def _row(warehouse, metric_id):
    return next(row for row in CatalogService(warehouse).rows() if row.id == metric_id)


def test_empty_warehouse_still_exposes_the_whole_dictionary(warehouse):
    service = CatalogService(warehouse)
    rows = service.rows()
    assert len(rows) > 200
    assert service.summary().latest_run is None
    assert all(row.status in (
        Status.UNVERIFIED, Status.UNCOVERED, Status.UNKNOWN_MEANING
    ) for row in rows)


def test_passing_check_marks_metric_ok(warehouse):
    _check_rows(warehouse, [{
        "metric_id": "tushare_daily.amount",
        "check_id": "A1.dimension.amount_vs_volume_close",
        "passed": True,
        "severity": "warning",
    }])
    assert _row(warehouse, "tushare_daily.amount").status is Status.OK


def test_blocking_failure_beats_passing_sibling(warehouse):
    _check_rows(warehouse, [
        {"metric_id": "tushare_daily.amount", "check_id": "A1.x",
         "passed": True, "severity": "warning"},
        {"metric_id": "tushare_daily.amount", "check_id": "B3.null_rate",
         "passed": False, "severity": "blocking", "violations": 3},
    ])
    assert _row(warehouse, "tushare_daily.amount").status is Status.FAILED


def test_warning_failure_is_a_known_issue(warehouse):
    _check_rows(warehouse, [{
        "metric_id": "adj_factors.cum_factor", "check_id": "A3.missed_exdiv",
        "passed": False, "severity": "warning", "known_issue": "D-1",
        "violations": 12,
    }])
    row = _row(warehouse, "adj_factors.cum_factor")
    assert row.status is Status.KNOWN_ISSUE
    assert "D-1" in row.issues


def test_uncovered_metrics_are_not_hidden(warehouse):
    assert CatalogService(warehouse).rows(status=Status.UNCOVERED)


def test_default_order_puts_failures_before_ok(warehouse):
    _check_rows(warehouse, [
        {"metric_id": "tushare_daily.amount", "check_id": "A1.x",
         "passed": False, "severity": "blocking", "violations": 1},
        {"metric_id": "tushare_daily.close", "check_id": "A1.y",
         "passed": True, "severity": "warning"},
    ])
    statuses = [row.status for row in CatalogService(warehouse).rows()]
    assert statuses[0] is Status.FAILED
    assert statuses.index(Status.FAILED) < statuses.index(Status.OK)


def test_direct_source_label_reaches_interface_field_and_multiplier(warehouse):
    label = _row(warehouse, "tushare_daily.amount").source_label
    assert "daily_basic" not in label
    assert "daily.amount" in label
    assert "1000" in label


def test_derived_source_label_uses_root_sources(warehouse):
    assert "tushare.daily" in _row(
        warehouse, "daily_flags.is_first_board"
    ).source_label


def test_summary_pending_excludes_known_issues(warehouse):
    service = CatalogService(warehouse)
    assert service.summary().pending == sum(
        row.status in (Status.FAILED, Status.UNKNOWN_MEANING, Status.UNCOVERED)
        for row in service.rows()
    )


def test_filters_compose(warehouse):
    rows = CatalogService(warehouse).rows(dataset="daily_flags", kind="derived")
    assert rows
    assert all(row.dataset == "daily_flags" and str(row.kind) == "derived"
               for row in rows)
