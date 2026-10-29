from datetime import date, datetime, timedelta

import polars as pl

from qsys.data import schema
from qsys.data.catalog.input_versions import dataset_binding
from qsys.data.catalog.health import (
    ALL_DIMENSIONS,
    HealthEvidence,
    HealthReason,
    HealthStatus,
    dataset_check_frame,
    write_dataset_checks,
)
from qsys.data.cutover import (
    BenchmarkComparison,
    CutoverConfig,
    CutoverMarketData,
    FacadeMode,
    StrategyComparison,
    apply_facade_argument,
    audit_cutover,
)
from qsys.core.calendar import AShareCalendar
from qsys.data.feed import HistoricalFeed
from qsys.data.minute_feed import MinuteFeed
from qsys.data.query import WarehouseQuery


DAY = date(2026, 9, 21)
AT = datetime(2026, 9, 21, 9, 31)
SYMBOL = "600036.SH"


def _seed(warehouse):
    old_daily = {name: None for name in schema.TUSHARE_DAILY.names}
    old_daily.update({
        "symbol": SYMBOL, "dt": DAY, "open": 9.9, "high": 10.1,
        "low": 9.8, "close": 10.0, "volume": 100.0, "amount": 100_000.0,
    })
    warehouse.write(
        "tushare_daily",
        pl.DataFrame([old_daily]).select(schema.TUSHARE_DAILY.names)
        .to_arrow().cast(schema.TUSHARE_DAILY), year=DAY.year,
    )
    canonical_daily = pl.DataFrame([{**{
        key: old_daily[key] for key in schema.DAILY_BARS.names
    }, "pre_close": 9.7}]).select(schema.BAR_DAILY.names)
    warehouse.write(
        "bar_daily", canonical_daily.to_arrow().cast(schema.BAR_DAILY),
        year=DAY.year,
    )
    old_minute = pl.DataFrame([{
        "symbol": SYMBOL, "dt": AT, "open": 10.0, "high": 10.0,
        "low": 10.0, "close": 10.0, "volume": 10, "amount": 10_000.0,
    }])
    warehouse.write_day(
        "minute_bars", old_minute.to_arrow().cast(schema.MINUTE_BARS), day=DAY
    )
    canonical_minute = old_minute.rename({"dt": "ts"}).with_columns(
        pl.col("volume").cast(pl.Float64), pl.lit("qmt").alias("source"),
        pl.col("ts").alias("visible_at"),
    ).select(schema.BAR_MINUTE_1M.names)
    warehouse.write_day(
        "bar_minute_1m",
        canonical_minute.to_arrow().cast(schema.BAR_MINUTE_1M), day=DAY,
    )


def _green_health(warehouse, datasets):
    now = datetime(2026, 9, 21, 18)
    for dataset in datasets:
        evidence = tuple(
            HealthEvidence(
                dimension, HealthStatus.GREEN, f"test.{dimension.value}",
                now, now + timedelta(days=1),
                HealthReason("test", "green", dimension), checked_rows=1,
            )
            for dimension in ALL_DIMENSIONS
        )
        write_dataset_checks(
            warehouse, dataset_check_frame("cutover-test", now, dataset, evidence,
                scope_start=DAY, scope_end=DAY,
                input_version=dataset_binding(warehouse, dataset, DAY, DAY))
        )


def test_cutover_facade_defaults_to_legacy_and_never_falls_back(warehouse, monkeypatch):
    _seed(warehouse)
    monkeypatch.delenv("QSYS_MARKET_DATA_FACADE", raising=False)
    assert CutoverConfig.from_env().mode is FacadeMode.LEGACY
    query = WarehouseQuery(warehouse)
    legacy = CutoverMarketData(query, CutoverConfig())
    canonical = CutoverMarketData(query, CutoverConfig(FacadeMode.CANONICAL))
    assert legacy.daily_bars(None, DAY, DAY).columns == schema.DAILY_BARS.names
    assert canonical.daily_bars(None, DAY, DAY).columns == schema.DAILY_BARS.names
    legacy_minute = legacy.minute_bars(
        None, datetime.combine(DAY, datetime.min.time()),
        datetime.combine(DAY, datetime.max.time()),
    )
    canonical_minute = canonical.minute_bars(
        None, datetime.combine(DAY, datetime.min.time()),
        datetime.combine(DAY, datetime.max.time()),
    )
    assert legacy_minute["volume"].dtype == pl.Float64
    assert canonical_minute["volume"].dtype == pl.Float64
    assert legacy_minute.equals(canonical_minute)
    assert query.historical_daily_bars(
        None, DAY, DAY, columns=("symbol", "close")
    ).columns == ["symbol", "close"]
    assert query.historical_minute_bars(
        None, datetime.combine(DAY, datetime.min.time()),
        datetime.combine(DAY, datetime.max.time()), columns=("symbol", "ts"),
    ).columns == ["symbol", "ts"]


def test_canonical_switch_does_not_fall_back_to_populated_legacy(warehouse):
    old = {name: None for name in schema.TUSHARE_DAILY.names}
    old.update({"symbol": SYMBOL, "dt": DAY, "close": 10.0})
    warehouse.write(
        "tushare_daily",
        pl.DataFrame([old]).select(schema.TUSHARE_DAILY.names)
        .to_arrow().cast(schema.TUSHARE_DAILY), year=DAY.year,
    )
    facade = CutoverMarketData(
        WarehouseQuery(warehouse), CutoverConfig(FacadeMode.CANONICAL)
    )
    assert facade.daily_bars(None, DAY, DAY).height == 0


def test_research_feeds_use_the_explicit_canonical_facade(warehouse):
    _seed(warehouse)
    query = WarehouseQuery(warehouse)
    market_data = CutoverMarketData(
        query, CutoverConfig(FacadeMode.CANONICAL)
    )
    calendar = AShareCalendar([DAY])

    daily = HistoricalFeed(query, calendar, market_data).get(
        [SYMBOL], DAY, DAY,
        as_of=datetime.combine(DAY, datetime.max.time()),
    )
    minute = MinuteFeed(query, market_data).get(
        [SYMBOL], AT, AT, as_of=AT,
    )

    assert daily.height == 1
    assert daily.columns == schema.DAILY_BARS.names
    assert minute.height == 1
    assert minute["volume"].dtype == pl.Float64


def test_invalid_cutover_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("QSYS_MARKET_DATA_FACADE", "auto")
    try:
        CutoverConfig.from_env()
    except ValueError as exc:
        assert "legacy 或 canonical" in str(exc)
    else:
        raise AssertionError("非法模式不应被接受")


def test_cli_facade_override_updates_the_shared_process_setting(monkeypatch):
    monkeypatch.setenv("QSYS_MARKET_DATA_FACADE", "legacy")

    config = apply_facade_argument("canonical")

    assert config.mode is FacadeMode.CANONICAL
    assert CutoverConfig.from_env().mode is FacadeMode.CANONICAL


def test_performance_evidence_checks_time_scan_shape_and_memory():
    rows = {"legacy_rows": 100, "canonical_rows": 100}
    baseline = BenchmarkComparison(
        "daily", 10.0, 10.0, 2, 2, 8, 8, 20.0, 20.0, **rows
    )
    assert baseline.passed
    assert not BenchmarkComparison(
        "slower", 10.0, 10.1, 2, 2, 8, 8, 20.0, 20.0, **rows
    ).passed
    assert not BenchmarkComparison(
        "more-files", 10.0, 9.0, 2, 3, 8, 8, 20.0, 19.0, **rows
    ).passed
    assert not BenchmarkComparison(
        "more-columns", 10.0, 9.0, 2, 2, 8, 9, 20.0, 19.0, **rows
    ).passed
    assert not BenchmarkComparison(
        "more-memory", 10.0, 9.0, 2, 2, 8, 8, 20.0, 20.1, **rows
    ).passed
    assert not BenchmarkComparison(
        "empty-target", 0.1, 0.1, 0, 0, 8, 8, 20.0, 20.0,
        legacy_rows=100, canonical_rows=0,
    ).passed
    assert not BenchmarkComparison(
        "missing-row", 10.0, 9.0, 2, 2, 8, 8, 20.0, 19.0,
        legacy_rows=100, canonical_rows=99,
    ).passed


def test_empty_warehouse_is_blocked_without_mutation(warehouse):
    report = audit_cutover(
        WarehouseQuery(warehouse), DAY, DAY,
        commit_sha="bad", release_clean=False, performance=None, strategy=None,
        required_datasets=("bar_daily", "bar_minute_1m"), min_cycle_sessions=1,
    )
    assert not report.ready
    assert {item.id for item in report.checks if not item.passed} == {
        "release_revision", "canonical_backfill", "facade_reconciliation",
        "required_health", "performance", "strategy_inputs",
        "observation_cycle", "rollback_path",
    }
    assert not any(warehouse.root.rglob("*.parquet"))


def test_all_cutover_conditions_can_pass_with_explicit_evidence(warehouse):
    _seed(warehouse)
    _green_health(warehouse, ("bar_daily", "bar_minute_1m"))
    report = audit_cutover(
        WarehouseQuery(warehouse), DAY, DAY,
        commit_sha="a" * 40, release_clean=True,
        performance=(
            BenchmarkComparison(
                "daily", 10.0, 9.0, 2, 1, 8, 8, 20.0, 18.0,
                legacy_rows=1, canonical_rows=1,
            ),
            BenchmarkComparison(
                "minute", 20.0, 20.0, 5, 1, 8, 8, 30.0, 30.0,
                legacy_rows=1, canonical_rows=1,
            ),
        ),
        strategy=StrategyComparison(
            True, 3, "same-digest", "same-digest", "三组策略输入逐键一致"
        ),
        required_datasets=("bar_daily", "bar_minute_1m"),
        min_cycle_sessions=1, now=datetime(2026, 9, 21, 20),
    )
    assert report.ready
    assert all(item.passed for item in report.checks)
    assert report.to_dict()["ready"] is True
