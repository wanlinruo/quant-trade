from datetime import date

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.pipeline.canonical_daily import (
    build_adjustment_factor_daily,
    build_bar_daily,
    build_share_capital_history,
    build_valuation_daily,
    canonical_daily_preflight,
    daily_reconciliation,
)
from qsys.data.query import WarehouseQuery


D1, D2 = date(2026, 9, 17), date(2026, 9, 18)


def _daily_row(symbol: str, day: date, close: float) -> dict:
    row = {name: None for name in schema.TUSHARE_DAILY.names}
    row.update({
        "dt": day, "symbol": symbol, "name": None,
        "open": close - 0.2, "high": close + 0.3, "low": close - 0.4,
        "close": close, "pre_close": close - 0.1, "change": 0.1,
        "pct_change": 1.0, "volume": 1234.5, "amount": 987654.0,
        "factor": 1.25, "turnover_ratio": 2.1, "free_turnover_ratio": 3.2,
        "volume_ratio": 1.1, "pe": 10.0, "pe_ttm": 11.0, "pb": 1.2,
        "ps": 2.0, "ps_ttm": 2.1, "dividend_yield": None,
        "dividend_yield_ttm": 0.5, "total_shares": 1_000_000.0,
        "circulating_shares": 800_000.0, "free_shares": 600_000.0,
        "market_cap": 10_000_000.0, "circulating_market_cap": 8_000_000.0,
    })
    return row


def _seed(warehouse):
    daily = pl.DataFrame([
        _daily_row("000001.SZ", D1, 10.0),
        _daily_row("000001.SZ", D2, 10.5),
    ]).select(schema.TUSHARE_DAILY.names).to_arrow().cast(schema.TUSHARE_DAILY)
    warehouse.write("tushare_daily", daily, year=2026)
    capital = pl.DataFrame([
        {"symbol": "000001.SZ", "dt": D1, "ann_date": date(2026, 9, 10),
         "total": 1_000_000.0, "circulating": 800_000.0},
    ]).to_arrow().cast(schema.SHARE_CAPITAL)
    warehouse.write("share_capital", capital)
    factors = pl.DataFrame([
        {"symbol": "000001.SZ", "dt": D1, "cum_factor": 1.2,
         "cum_share_factor": 1.0},
        {"symbol": "000001.SZ", "dt": D2, "cum_factor": 1.25,
         "cum_share_factor": 1.0},
    ]).to_arrow().cast(schema.ADJ_FACTORS)
    warehouse.write("adj_factors", factors)


def test_builds_four_narrow_tables_and_reconciles_every_value(warehouse):
    _seed(warehouse)
    assert canonical_daily_preflight(warehouse, D1, D2) == ()
    assert build_bar_daily(warehouse, D1, D2) == 2
    assert build_valuation_daily(warehouse, D1, D2) == 2
    assert build_share_capital_history(warehouse) == 1
    assert build_adjustment_factor_daily(warehouse, D1, D2) == 2
    results = daily_reconciliation(warehouse, D1, D2)
    assert all(result.matches for result in results.values())


def test_narrow_tables_do_not_mix_business_topics(warehouse):
    _seed(warehouse)
    build_bar_daily(warehouse, D1, D2)
    build_valuation_daily(warehouse, D1, D2)
    query = WarehouseQuery(warehouse)
    assert query.bar_daily(None, D1, D2).columns == schema.BAR_DAILY.names
    assert query.valuation_daily(None, D1, D2).columns == schema.VALUATION_DAILY.names
    assert "close" not in schema.VALUATION_DAILY.names
    assert "market_cap" not in schema.BAR_DAILY.names
    assert query.bar_daily(None, D1, D2)["volume"].to_list() == [1234.5, 1234.5]


def test_null_valuation_is_preserved_not_filled(warehouse):
    _seed(warehouse)
    build_valuation_daily(warehouse, D1, D2)
    got = WarehouseQuery(warehouse).valuation_daily(None, D1, D2)
    assert got["dividend_yield"].null_count() == 2


def test_share_capital_uses_first_visible_daily_basic_snapshot(warehouse):
    _seed(warehouse)
    build_share_capital_history(warehouse)
    got = WarehouseQuery(warehouse).share_capital_history()
    assert got.height == 1
    assert got["effective_date"].to_list() == [D1]
    assert got["announcement_date"].to_list() == [None]


def test_share_capital_compresses_daily_basic_changes_and_ignores_frozen_qmt(warehouse):
    _seed(warehouse)
    third = _daily_row("000001.SZ", date(2026, 9, 19), 10.7)
    third["total_shares"] = 1_100_000.0
    daily = pl.DataFrame([
        _daily_row("000001.SZ", D1, 10.0),
        _daily_row("000001.SZ", D2, 10.5),
        third,
    ]).select(schema.TUSHARE_DAILY.names).to_arrow().cast(schema.TUSHARE_DAILY)
    warehouse.write("tushare_daily", daily, year=2026)

    assert build_share_capital_history(warehouse) == 2
    got = WarehouseQuery(warehouse).share_capital_history()
    assert got["effective_date"].to_list() == [D1, date(2026, 9, 19)]
    assert got["total_shares"].to_list() == [1_000_000.0, 1_100_000.0]
    assert got["announcement_date"].null_count() == 2


def test_existing_daily_bars_consumer_is_not_switched(warehouse):
    _seed(warehouse)
    fake = pl.DataFrame([{
        "symbol": "000001.SZ", "dt": D1, "pre_close": 1.0,
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 999.0,
        "volume": 1.0, "amount": 1.0,
    }]).to_arrow().cast(schema.BAR_DAILY)
    warehouse.write("bar_daily", fake, year=2026)
    assert WarehouseQuery(warehouse).daily_bars(None, D1, D1)["close"][0] == 10.0


def test_duplicate_source_keys_block_publication(warehouse):
    row = _daily_row("000001.SZ", D1, 10.0)
    duplicate = pl.DataFrame([row, row]).select(
        schema.TUSHARE_DAILY.names
    ).to_arrow().cast(schema.TUSHARE_DAILY)
    warehouse.write("tushare_daily", duplicate, year=2026)
    with pytest.raises(RuntimeError, match="重复键"):
        build_bar_daily(warehouse, D1, D1)
    assert WarehouseQuery(warehouse).bar_daily(None, D1, D1).height == 0


def test_preflight_reports_every_missing_source_before_partial_publish(warehouse):
    issues = canonical_daily_preflight(warehouse, D1, D2)
    assert any("tushare_daily" in item for item in issues)
    assert any("adj_factors" in item for item in issues)
    assert not (warehouse.root / "bar_daily").exists()


def test_adjustment_factor_query_pushes_down_date_range(warehouse):
    _seed(warehouse)
    query = WarehouseQuery(warehouse)
    assert query.adj_factors(start=D2, end=D2)["dt"].to_list() == [D2]
    with pytest.raises(ValueError, match="together"):
        query.adj_factors(start=D1)
