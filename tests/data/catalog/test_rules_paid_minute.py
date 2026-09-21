import json
from datetime import date, datetime

import polars as pl

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.pipeline.minute_repairs import expected_minute_times
from qsys.data.query import WarehouseQuery

OVERRIDE_DAY = date(2026, 1, 29)


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


def test_annual_shards_without_summary_fail_structure(warehouse):
    target = warehouse.root / "paid_minute_bars/year=2024/shard=0000"
    target.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600000.SH"]}).write_parquet(target / "part.parquet")
    _write_summary(warehouse)

    row = _run("B2.paid_minute_structure", warehouse)

    assert row["passed"] is False
    assert row["severity"] == "blocking"
    assert row["violations"] == 1


def _override_frame(symbols, day=OVERRIDE_DAY):
    times = expected_minute_times(day)
    return pl.concat([
        pl.DataFrame({
            "symbol": [symbol] * len(times), "dt": times,
            "open": [10.0] * len(times), "high": [10.1] * len(times),
            "low": [9.9] * len(times), "close": [10.0] * len(times),
            "volume": [100] * len(times), "amount": [100_000.0] * len(times),
            "factor": [1.5] * len(times),
        })
        for symbol in symbols
    ])


def _write_override(warehouse, frame, day=OVERRIDE_DAY):
    target = warehouse.root / f"paid_minute_bars/year={day.year}/date={day:%Y%m%d}"
    target.mkdir(parents=True)
    frame.write_parquet(target / "part.parquet")


def test_date_overrides_are_rechecked_per_symbol_day(warehouse):
    _write_override(warehouse, pl.concat([
        _override_frame(["300344.SZ"]),
        _override_frame(["000004.SZ"]).filter(pl.col("dt") != pl.col("dt").max()),
    ]))

    row = _run("B2.paid_minute_override_structure", warehouse)

    assert row["passed"] is False
    assert row["severity"] == "blocking"
    assert row["violations"] == 1
    assert row["checked_rows"] == 2


def test_valid_date_overrides_pass_structure(warehouse):
    _write_override(warehouse, _override_frame(["300344.SZ", "000004.SZ"]))

    row = _run("B2.paid_minute_override_structure", warehouse)

    assert row["passed"] is True
    assert row["checked_rows"] == 2


def _write_override_daily(warehouse, symbols, day=OVERRIDE_DAY, **overrides):
    rows = []
    for symbol in symbols:
        row = {field.name: 1.0 for field in list(schema.TUSHARE_DAILY)[3:]}
        row.update({"dt": day, "symbol": symbol, "name": symbol, "open": 10.0,
                    "high": 10.1, "low": 9.9, "close": 10.0, "volume": 241.0,
                    "amount": 241 * 100_000.0})
        row.update(overrides.get(symbol, {}))
        rows.append(row)
    warehouse.write(
        "tushare_daily",
        pl.DataFrame(rows).select(schema.TUSHARE_DAILY.names).to_arrow().cast(
            schema.TUSHARE_DAILY
        ),
        year=day.year,
    )


def test_override_volume_blowup_blocks_and_rounding_warns(warehouse):
    _write_override(warehouse, _override_frame(["300344.SZ", "000004.SZ", "600000.SH"]))
    _write_override_daily(warehouse, ["300344.SZ", "000004.SZ", "600000.SH"], **{
        "000004.SZ": {"volume": 241.0 / 77},
        "600000.SH": {"volume": 240.94},
    })

    gross = _run("A3.paid_minute_override_gross_vs_daily", warehouse)
    minor = _run("A3.paid_minute_override_vs_daily", warehouse)

    assert gross["passed"] is False
    assert gross["severity"] == "blocking"
    assert gross["violations"] == 1
    assert gross["checked_rows"] == 3
    assert minor["passed"] is False
    assert minor["severity"] == "warning"
    assert minor["known_issue"] == "D-20"
    assert minor["violations"] == 1


def test_override_without_daily_row_blocks(warehouse):
    _write_override(warehouse, _override_frame(["300344.SZ", "000004.SZ"]))
    _write_override_daily(warehouse, ["300344.SZ"])

    row = _run("A3.paid_minute_override_gross_vs_daily", warehouse)

    assert row["passed"] is False
    assert row["violations"] == 1


def test_consistent_overrides_pass_daily_checks(warehouse):
    _write_override(warehouse, _override_frame(["300344.SZ"]))
    _write_override_daily(warehouse, ["300344.SZ"])

    for check_id in ("A3.paid_minute_override_gross_vs_daily",
                     "A3.paid_minute_override_vs_daily"):
        assert _run(check_id, warehouse)["passed"] is True


def test_override_structure_not_applicable_without_overrides(warehouse):
    row = _run("B2.paid_minute_override_structure", warehouse)
    assert row["passed"] is True
    assert "不适用" in row["detail"]


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
        ["600001.SH", "600002.SH", "603227.SH", "920001.BJ", "302132.SZ"],
    )

    row = _run("B2.paid_minute_symbol_coverage", warehouse)

    assert row["passed"] is False
    assert row["severity"] == "blocking"
    assert row["violations"] == 1
    # 600002.SH 不在名单内，阻断；603227.SH 在 D-21 名单内，只由已知缺陷规则报警告。
    known = _run("B2.paid_minute_known_symbol_gap", warehouse)
    assert known["passed"] is False
    assert known["severity"] == "warning"
    assert known["known_issue"] == "D-21"
    assert known["violations"] == 1


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
