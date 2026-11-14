from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.pipeline.canonical_state import (
    build_market_flags_daily,
    build_market_regime_daily,
    build_price_limit_daily,
    build_security_status_daily,
    canonical_state_preflight,
    state_reconciliation,
)
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.query import WarehouseQuery


D1, D2 = date(2026, 9, 17), date(2026, 9, 18)
SYMBOL = "000001.SZ"


def _daily(day: date, close: float, pre_close: float) -> dict:
    row = {name: None for name in schema.TUSHARE_DAILY.names}
    row.update({
        "dt": day, "symbol": SYMBOL, "open": pre_close, "high": close,
        "low": pre_close, "close": close, "pre_close": pre_close,
        "volume": 100.0, "amount": 1000.0, "factor": 1.0,
    })
    return row


def _seed(warehouse) -> None:
    daily = pl.DataFrame([
        _daily(D1, 10.0, 9.5), _daily(D2, 11.0, 10.0),
    ]).select(schema.TUSHARE_DAILY.names)
    warehouse.write(
        "tushare_daily", daily.to_arrow().cast(schema.TUSHARE_DAILY), year=2026
    )
    warehouse.write(
        "bar_daily", daily.select(schema.BAR_DAILY.names).to_arrow().cast(
            schema.BAR_DAILY
        ), year=2026,
    )
    limits = pl.DataFrame([
        {"symbol": SYMBOL, "dt": D1, "pre_close": 9.5,
         "limit_up": 10.45, "limit_down": 8.55},
        {"symbol": SYMBOL, "dt": D2, "pre_close": 10.0,
         "limit_up": 11.0, "limit_down": 9.0},
    ])
    warehouse.write(
        "price_limits_daily", limits.to_arrow().cast(schema.PRICE_LIMITS_DAILY),
        year=2026,
    )
    st = pl.DataFrame([
        {"symbol": SYMBOL, "dt": day, "is_st": False, "name": "平安银行",
         "st_type": None, "st_type_name": None}
        for day in (D1, D2)
    ])
    warehouse.write(
        "stock_st_daily", st.to_arrow().cast(schema.STOCK_ST_DAILY), year=2026
    )
    listed = pl.DataFrame([{
        "symbol": SYMBOL, "dt": D2, "limit_type": "U", "limit_times": 1,
        "open_times": 0, "first_time": "093000", "fd_amount": 1.0,
        "limit_amount": 1.0, "float_mv": 1.0,
    }])
    warehouse.write(
        "limit_list_daily", listed.to_arrow().cast(schema.LIMIT_LIST_DAILY), year=2026
    )
    warehouse.write("suspend_daily", schema.SUSPEND_DAILY.empty_table(), year=2026)
    warehouse.write("delisting_pool", schema.DELISTING_POOL.empty_table(), year=2026)
    old_factors = pl.DataFrame([
        {"symbol": SYMBOL, "dt": day, "cum_factor": 1.0,
         "cum_share_factor": 1.0}
        for day in (D1, D2)
    ])
    warehouse.write("adj_factors", old_factors.to_arrow().cast(schema.ADJ_FACTORS))
    canonical_factors = old_factors.rename({
        "cum_factor": "price_factor", "cum_share_factor": "share_factor",
    })
    warehouse.write(
        "adjustment_factor_daily",
        canonical_factors.to_arrow().cast(schema.ADJUSTMENT_FACTOR_DAILY), year=2026,
    )


def test_builds_stage5_chain_and_reconciles_old_outputs(warehouse):
    _seed(warehouse)
    build_daily_flags(warehouse, D1, D2)
    build_market_regime(warehouse, D1, D2)
    assert canonical_state_preflight(warehouse, D1, D2) == ()
    assert build_price_limit_daily(warehouse, D1, D2) == 2
    assert build_security_status_daily(warehouse, D1, D2) == 2
    assert build_market_flags_daily(warehouse, D1, D2) == 2
    assert build_market_regime_daily(warehouse, D1, D2) == 2
    results = state_reconciliation(warehouse, D1, D2)
    assert all(result.matches for result in results.values()), results


def test_unknown_status_is_not_filled_false(warehouse):
    _seed(warehouse)
    suspended = pl.DataFrame([{
        "symbol": "000002.SZ", "dt": D2, "suspend_type": "S",
        "suspend_timing": None,
    }])
    warehouse.write(
        "suspend_daily", suspended.to_arrow().cast(schema.SUSPEND_DAILY), year=2026
    )
    build_security_status_daily(warehouse, D1, D2)
    row = WarehouseQuery(warehouse).security_status_daily(
        ["000002.SZ"], D2, D2
    ).row(0, named=True)
    assert row["is_suspended"] is True
    assert row["is_st"] is None
    assert row["st_source"] is None


def test_same_day_suspend_and_resume_preserves_suspension_event(warehouse):
    _seed(warehouse)
    events = pl.DataFrame([
        {"symbol": SYMBOL, "dt": D2, "suspend_type": "S",
         "suspend_timing": "9:30-9:40"},
        {"symbol": SYMBOL, "dt": D2, "suspend_type": "R",
         "suspend_timing": None},
    ])
    warehouse.write(
        "suspend_daily", events.to_arrow().cast(schema.SUSPEND_DAILY), year=2026
    )

    assert canonical_state_preflight(warehouse, D1, D2) == ()
    build_security_status_daily(warehouse, D1, D2)
    rows = WarehouseQuery(warehouse).security_status_daily([SYMBOL], D2, D2)

    assert rows.height == 1
    assert rows.row(0, named=True)["is_suspended"] is True
    assert rows.row(0, named=True)["suspension_source"] == "suspend_daily"


def test_existing_consumers_are_not_switched(warehouse):
    _seed(warehouse)
    fake = schema.MARKET_FLAGS_DAILY.empty_table()
    warehouse.write("market_flags_daily", fake, year=2026)
    assert WarehouseQuery(warehouse).daily_flags(D1, D2).height == 0
    assert WarehouseQuery(warehouse).market_flags_daily(None, D1, D2).height == 0


def test_preflight_distinguishes_unpublished_sparse_facts_from_no_events(warehouse):
    issues = canonical_state_preflight(warehouse, D1, D2)
    assert any("bar_daily" in item for item in issues)
    assert any("suspend_daily 尚未发布" in item for item in issues)
    assert any("delisting_pool 尚未发布" in item for item in issues)
    assert not (warehouse.root / "price_limit_daily").exists()


# ---- #659：prev_close 只在上市期内派生（维护者 2026-11-14）----
import pytest  # noqa: E402


def _master(warehouse, list_date):
    warehouse.write("security_master", pl.DataFrame({
        "symbol": [SYMBOL], "name": ["fixture"], "market": ["SZ"],
        "list_date": [list_date], "delist_date": [None], "board": ["main"],
        "as_of": [D2],
    }).to_arrow().cast(schema.SECURITY_MASTER))


def _prev_close(warehouse, day):
    query = WarehouseQuery(warehouse)
    legacy = query.daily_flags(start=day, end=day).filter(pl.col("symbol") == SYMBOL)
    canonical = query.market_flags_daily(None, day, day).filter(pl.col("symbol") == SYMBOL)
    return legacy["prev_close"][0], canonical["prev_close"][0]


def _build(warehouse):
    build_daily_flags(warehouse, D1, D2)
    build_market_flags_daily(warehouse, D1, D2)


def test_listing_day_prev_close_does_not_reach_pre_listing_rows(warehouse):
    """新三板时期的行挂在同一代码下：上市日没有“上一个上市交易日”，记 null。"""
    _seed(warehouse)
    _master(warehouse, D2)
    _build(warehouse)
    assert _prev_close(warehouse, D2) == (None, None)
    # adj_prev_close 仍是直取 pre_close，不受影响
    row = WarehouseQuery(warehouse).market_flags_daily(None, D2, D2).row(0, named=True)
    assert row["adj_prev_close"] == pytest.approx(10.0)


@pytest.mark.parametrize("list_date", [D1, date(2020, 1, 1), None])
def test_prev_close_within_listing_period_or_unknown_listing_is_unchanged(warehouse, list_date):
    _seed(warehouse)
    _master(warehouse, list_date)
    _build(warehouse)
    assert _prev_close(warehouse, D2) == (pytest.approx(10.0), pytest.approx(10.0))


def test_missing_security_master_keeps_existing_derivation(warehouse):
    _seed(warehouse)
    _build(warehouse)
    assert _prev_close(warehouse, D2) == (pytest.approx(10.0), pytest.approx(10.0))


# ---- #663：canonical 情绪与 legacy 一样只用上市期内的行 ----

def test_canonical_regime_excludes_pre_listing_rows_like_legacy(warehouse):
    """上市日之前挂在同代码下的历史行不能进入广度与收益中位数。"""
    _seed(warehouse)
    _master(warehouse, D2)
    build_daily_flags(warehouse, D1, D2)
    build_market_regime(warehouse, D1, D2)
    build_price_limit_daily(warehouse, D1, D2)
    build_security_status_daily(warehouse, D1, D2)
    build_market_flags_daily(warehouse, D1, D2)
    build_market_regime_daily(warehouse, D1, D2)
    results = state_reconciliation(warehouse, D1, D2)
    assert all(result.matches for result in results.values()), results
    query = WarehouseQuery(warehouse)
    legacy = query.market_regime(D2, D2).row(0, named=True)
    canonical = query.market_regime_daily(D2, D2).row(0, named=True)
    for column in ("market_advance_ratio", "market_median_return_1d"):
        assert canonical[column] == legacy[column], column
