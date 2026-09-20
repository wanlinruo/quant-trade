import json
from datetime import date, datetime

import polars as pl

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.query import WarehouseQuery


def _write_summary(warehouse, year=2025, **daily_overrides):
    daily = {
        "stock_days": 100,
        "matched_paid_days": 100,
        "missing_paid_days": 0,
        "non_241_regular_days": 0,
        "factor_not_unique_days": 0,
        "factor_mismatch_days": 0,
        "ohlc_mismatch_days": 0,
        "volume_mismatch_days": 0,
        "amount_mismatch_gt_100_days": 0,
    }
    daily.update(daily_overrides)
    target = warehouse.root / f"paid_minute_quality/year={year}"
    target.mkdir(parents=True)
    (target / "quality-summary.json").write_text(json.dumps({
        "year": year,
        "status": "PUBLISHED",
        "structural": {"invalid_session_times": 0},
        "daily_comparison": daily,
    }))


def _run(check_id, warehouse):
    ctx = C.CheckContext(
        WarehouseQuery(warehouse), warehouse, date(2026, 1, 1), date(2026, 9, 18)
    )
    return C.run_checks(
        ctx, "paid-minute-test", datetime(2026, 9, 19),
        only=[C.CHECKS[check_id]], artifacts=warehouse.root / "artifacts",
    ).row(0, named=True)


def test_published_complete_year_passes_structure(warehouse):
    _write_summary(warehouse)
    row = _run("B2.paid_minute_structure", warehouse)
    assert row["passed"] is True
    assert row["checked_rows"] == 100


def test_missing_stock_days_fail_structure(warehouse):
    _write_summary(warehouse, missing_paid_days=3)
    row = _run("B2.paid_minute_structure", warehouse)
    assert row["passed"] is False
    assert row["severity"] == "blocking"


def _write_tushare_symbols(warehouse, symbols):
    rows = []
    for symbol in symbols:
        row = {field.name: 1.0 for field in list(schema.TUSHARE_DAILY)[3:]}
        row.update({"dt": date(2025, 1, 2), "symbol": symbol, "name": symbol})
        rows.append(row)
    warehouse.write(
        "tushare_daily",
        pl.DataFrame(rows).select(schema.TUSHARE_DAILY.names).to_arrow().cast(
            schema.TUSHARE_DAILY
        ),
        year=2025,
    )


def test_whole_symbol_gap_fails_coverage_without_scanning_minute_facts(warehouse):
    _write_summary(warehouse)
    quality = warehouse.root / "paid_minute_quality/year=2025"
    pl.DataFrame({
        "symbol": ["600001.SH"], "dt": [date(2025, 1, 2)]
    }).write_parquet(quality / "daily_aggregate.parquet")
    _write_tushare_symbols(
        warehouse,
        ["600001.SH", "603227.SH", "920001.BJ", "302132.SZ"],
    )

    row = _run("B2.paid_minute_symbol_coverage", warehouse)

    assert row["passed"] is False
    assert row["severity"] == "blocking"
    assert row["violations"] == 1


def test_cross_source_differences_are_visible_known_issues(warehouse):
    _write_summary(
        warehouse,
        ohlc_mismatch_days=7,
        volume_mismatch_days=2,
        amount_mismatch_gt_100_days=1,
    )
    for check_id, violations in (
        ("A3.paid_minute_ohlc_vs_daily", 7),
        ("A3.paid_minute_volume_vs_daily", 2),
        ("A3.paid_minute_amount_vs_daily", 1),
    ):
        row = _run(check_id, warehouse)
        assert row["passed"] is False
        assert row["severity"] == "warning"
        assert row["known_issue"] == "D-20"
        assert row["violations"] == violations
