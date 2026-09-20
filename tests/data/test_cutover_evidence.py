from datetime import date, datetime

import polars as pl

from qsys.data import schema
from qsys.data.cutover_evidence import strategy_input_comparison
from qsys.data.pipeline.canonical_state import _security_status_frame


DAY = date(2026, 9, 21)
AT = datetime(2026, 9, 21, 9, 31)


def _seed(warehouse, canonical_close: float = 10.0):
    source = {name: None for name in schema.TUSHARE_DAILY.names}
    source.update({
        "symbol": "600036.SH", "dt": DAY, "open": 9.9, "high": 10.1,
        "low": 9.8, "close": 10.0, "volume": 100.0, "amount": 100_000.0,
    })
    warehouse.write(
        "tushare_daily",
        pl.DataFrame([source]).select(schema.TUSHARE_DAILY.names)
        .to_arrow().cast(schema.TUSHARE_DAILY), year=DAY.year,
    )
    target = {name: source.get(name) for name in schema.BAR_DAILY.names}
    target["pre_close"] = 9.7
    target["close"] = canonical_close
    warehouse.write(
        "bar_daily", pl.DataFrame([target]).select(schema.BAR_DAILY.names)
        .to_arrow().cast(schema.BAR_DAILY), year=DAY.year,
    )
    old_minute = pl.DataFrame([{
        "symbol": "600036.SH", "dt": AT, "open": 10.0, "high": 10.0,
        "low": 10.0, "close": 10.0, "volume": 10, "amount": 10_000.0,
    }])
    warehouse.write_day(
        "minute_bars", old_minute.to_arrow().cast(schema.MINUTE_BARS), day=DAY
    )
    new_minute = old_minute.rename({"dt": "ts"}).with_columns(
        pl.col("volume").cast(pl.Float64), pl.lit("qmt").alias("source"),
        pl.col("ts").alias("visible_at"),
    ).select(schema.BAR_MINUTE_1M.names)
    warehouse.write_day(
        "bar_minute_1m", new_minute.to_arrow().cast(schema.BAR_MINUTE_1M), day=DAY
    )
    status = _security_status_frame(warehouse, DAY, DAY)
    warehouse.write(
        "security_status_daily", status.to_arrow().cast(schema.SECURITY_STATUS_DAILY),
        year=DAY.year,
    )
    legacy_valuation = {
        "dt": DAY, "symbol": "600036.SH", "close": 10.0,
        "market_cap": 1000.0, "circulating_market_cap": 800.0,
        "turnover_ratio": 1.25,
    }
    warehouse.write(
        "valuation_snapshot",
        pl.DataFrame([legacy_valuation]).to_arrow().cast(schema.VALUATION_SNAPSHOT),
        year=DAY.year,
    )
    canonical_valuation = {name: None for name in schema.VALUATION_DAILY.names}
    canonical_valuation.update({
        "symbol": "600036.SH", "dt": DAY, "market_cap": 100_000_000_000.0,
        "circulating_market_cap": 80_000_000_000.0, "turnover_ratio": 1.25,
    })
    warehouse.write(
        "valuation_daily",
        pl.DataFrame([canonical_valuation]).select(schema.VALUATION_DAILY.names)
        .to_arrow().cast(schema.VALUATION_DAILY), year=DAY.year,
    )
    legacy_regime = {name: None for name in schema.MARKET_REGIME.names}
    legacy_regime.update({"dt": DAY, "index_ma20_slope_5d": 0.12345678901234})
    canonical_regime = dict(legacy_regime)
    canonical_regime["index_ma20_slope_5d"] = 0.12345678901235
    warehouse.write(
        "market_regime",
        pl.DataFrame([legacy_regime]).select(schema.MARKET_REGIME.names)
        .to_arrow().cast(schema.MARKET_REGIME), year=DAY.year,
    )
    warehouse.write(
        "market_regime_daily",
        pl.DataFrame([canonical_regime]).select(schema.MARKET_REGIME_DAILY.names)
        .to_arrow().cast(schema.MARKET_REGIME_DAILY), year=DAY.year,
    )


def test_strategy_evidence_hashes_all_normalized_input_families(warehouse):
    _seed(warehouse)
    result = strategy_input_comparison(warehouse, DAY, DAY)
    assert result.matches
    assert result.compared_cases > 0
    assert result.legacy_digest == result.canonical_digest
    assert "daily_bars: old/new=1/1" in result.detail
    assert "security_status: old/new=1/1" in result.detail
    assert "valuation: old/new=1/1" in result.detail
    assert "market_regime: old/new=1/1" in result.detail


def test_strategy_evidence_detects_value_drift(warehouse):
    _seed(warehouse, canonical_close=10.2)
    result = strategy_input_comparison(warehouse, DAY, DAY)
    assert not result.matches
    assert result.legacy_digest != result.canonical_digest
    assert "daily_bars: old/new=1/1" in result.detail
    assert "mismatched=1" in result.detail
