"""A2 独立重算；测试同时守住不调用生产计算函数。"""

import inspect
from datetime import date, datetime

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_recompute
from qsys.data.query import WarehouseQuery

D = [date(2026, 9, day) for day in (10, 11)]
NOW = datetime(2026, 9, 15, 19, 30)


@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(WarehouseQuery(warehouse), warehouse, D[0], D[-1])


def _flags(rows, warehouse):
    frame = pl.DataFrame(rows)
    types = {field.name: field.type for field in schema.DAILY_FLAGS}
    for name in schema.DAILY_FLAGS.names:
        if name not in frame.columns:
            dtype = pl.Boolean if str(types[name]) == "bool" else pl.Utf8 if str(types[name]) == "string" else pl.Float64
            frame = frame.with_columns(pl.lit(None, dtype=dtype).alias(name))
    warehouse.write("daily_flags", frame.select(schema.DAILY_FLAGS.names)
                    .to_arrow().cast(schema.DAILY_FLAGS), year=2026)


def _daily(rows, warehouse):
    frame = pl.DataFrame(rows)
    frame = frame.with_columns([
        pl.lit(None, dtype=pl.Utf8 if name == "name" else pl.Float64).alias(name)
        for name in schema.TUSHARE_DAILY.names if name not in frame.columns
    ]).select(schema.TUSHARE_DAILY.names)
    warehouse.write("tushare_daily", frame.to_arrow().cast(schema.TUSHARE_DAILY), year=2026)


def _regime(row, warehouse):
    frame = pl.DataFrame([row]).with_columns([
        pl.lit(None, dtype=pl.Int64 if str(schema.MARKET_REGIME.field(name).type) == "int64"
               else pl.Float64).alias(name)
        for name in schema.MARKET_REGIME.names if name not in row
    ]).select(schema.MARKET_REGIME.names)
    warehouse.write("market_regime", frame.to_arrow().cast(schema.MARKET_REGIME), year=2026)


def _run(ctx, check_id):
    return C.run_checks(
        ctx,
        "recompute",
        NOW,
        only=[C.CHECKS[check_id]],
        artifacts=ctx.warehouse.root / "test-artifacts",
    ).row(0, named=True)


def test_module_never_imports_production_calculators():
    source = inspect.getsource(rules_recompute)
    assert "pipeline.flags" not in source
    assert "pipeline.valuation" not in source
    assert "build_daily_flags" not in source


def test_limit_up_accepts_correct_row(ctx, warehouse):
    """没有直取价时走兜底档位。is_st 必须给——不知道是不是 ST 就定不出档位。"""
    _flags([{"dt": D[0], "symbol": "600036.SH", "adj_prev_close": 10.0,
             "limit_pct": .10, "limit_up": 11.0, "is_st": False}], warehouse)
    assert _run(ctx, "A2.recompute.limit_up")["passed"] is True


def test_limit_up_reports_wrong_st_band(ctx, warehouse):
    """迁移期（无直取价）的主板 ST 仍是 5% 档，11.0 对不上 10.5。"""
    _flags([{"dt": D[0], "symbol": "600001.SH", "adj_prev_close": 10.0,
             "limit_pct": .05, "limit_up": 11.0, "is_st": True}], warehouse)
    assert _run(ctx, "A2.recompute.limit_up")["violations"] == 1


def test_first_board_uses_previous_session(ctx, warehouse):
    _flags([
        {"dt": D[0], "symbol": "600036.SH", "is_limit_up": False, "is_first_board": False},
        {"dt": D[1], "symbol": "600036.SH", "is_limit_up": True, "is_first_board": True},
    ], warehouse)
    row = _run(ctx, "A2.recompute.is_first_board")
    assert row["passed"] is True and row["checked_rows"] == 2


def test_missing_upstream_is_excluded_not_filled(ctx, warehouse):
    _flags([{"dt": D[0], "symbol": "600036.SH", "adj_prev_close": None,
             "limit_pct": .10, "limit_up": 11.0}], warehouse)
    row = _run(ctx, "A2.recompute.limit_up")
    assert row["checked_rows"] == 0 and row["passed"] is False


def test_regime_count_is_rebuilt_from_daily_flags(ctx, warehouse):
    _daily([
        {"symbol": "600036.SH", "dt": D[0], "close": 11.0, "amount": 100.0},
        {"symbol": "000001.SZ", "dt": D[0], "close": 9.0, "amount": 200.0},
    ], warehouse)
    _flags([
        {"symbol": "600036.SH", "dt": D[0], "prev_close": 10.0,
         "is_limit_up": True, "is_limit_down": False, "touched_limit_up": True,
         "is_broken_board": False, "is_first_board": True},
        {"symbol": "000001.SZ", "dt": D[0], "prev_close": 10.0,
         "is_limit_up": False, "is_limit_down": False, "touched_limit_up": False,
         "is_broken_board": False, "is_first_board": False},
    ], warehouse)
    _regime({"dt": D[0], "limit_up_count": 99}, warehouse)
    assert _run(ctx, "A2.recompute.limit_up_count")["violations"] == 1


def test_all_thirty_recompute_rules_are_registered():
    ids = {item for item in C.CHECKS if item.startswith("A2.recompute.")}
    assert len(ids) == 30
    assert all(C.CHECKS[item].category is C.Category.TRUTHFULNESS for item in ids)


# --- 2026-09-15 直取换源：档位不再由规则派生，ST 不再由名称派生 ---------------


def _limits(rows, warehouse):
    frame = pl.DataFrame(rows)
    for name in schema.PRICE_LIMITS_DAILY.names:
        if name not in frame.columns:
            frame = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
    warehouse.write("price_limits_daily", frame.select(schema.PRICE_LIMITS_DAILY.names)
                    .to_arrow().cast(schema.PRICE_LIMITS_DAILY), year=2026)


def _stock_st(rows, warehouse):
    frame = pl.DataFrame(rows)
    for name, dtype in (("name", pl.Utf8), ("st_type", pl.Utf8), ("st_type_name", pl.Utf8)):
        if name not in frame.columns:
            frame = frame.with_columns(pl.lit(None, dtype=dtype).alias(name))
    warehouse.write("stock_st_daily", frame.select(schema.STOCK_ST_DAILY.names)
                    .to_arrow().cast(schema.STOCK_ST_DAILY), year=2026)


def test_limit_pct_accepts_a_gem_st_stock_at_twenty_percent(ctx, warehouse):
    """创业板 ST 的真实档位是 20%，不是 5%。

    旧规则按「is_st → 0.05」重算，会把这一行判成违例；而它恰恰是对的。
    """
    _flags([{"dt": D[0], "symbol": "300029.SZ", "adj_prev_close": 7.96,
             "limit_pct": .20, "limit_up": 9.55, "limit_down": 6.37,
             "is_st": True}], warehouse)
    _limits([{"symbol": "300029.SZ", "dt": D[0], "pre_close": 7.96,
              "limit_up": 9.55, "limit_down": 6.37}], warehouse)
    assert _run(ctx, "A2.recompute.limit_pct")["passed"] is True


def test_limit_pct_accepts_a_main_board_st_stock_at_ten_percent(ctx, warehouse):
    """2026-07-06 起主板 ST 的上限是 10%，与普通股相同。"""
    _flags([{"dt": D[0], "symbol": "000010.SZ", "adj_prev_close": 1.68,
             "limit_pct": .10, "limit_up": 1.85, "limit_down": 1.51,
             "is_st": True}], warehouse)
    _limits([{"symbol": "000010.SZ", "dt": D[0], "pre_close": 1.68,
              "limit_up": 1.85, "limit_down": 1.51}], warehouse)
    assert _run(ctx, "A2.recompute.limit_pct")["passed"] is True


def test_limit_pct_reports_a_tier_that_cannot_reproduce_the_price(ctx, warehouse):
    """档位必须能精确还原出涨停价，还原不出就是错的。"""
    _flags([{"dt": D[0], "symbol": "300029.SZ", "adj_prev_close": 7.96,
             "limit_pct": .05, "limit_up": 9.55, "limit_down": 6.37,
             "is_st": True}], warehouse)
    _limits([{"symbol": "300029.SZ", "dt": D[0], "pre_close": 7.96,
              "limit_up": 9.55, "limit_down": 6.37}], warehouse)
    assert _run(ctx, "A2.recompute.limit_pct")["violations"] == 1


def test_limit_up_expects_the_direct_price_not_a_band_derivation(ctx, warehouse):
    """有直取价时，涨停价的期望值就是直取价本身。

    旧规则拿 adj_prev_close × limit_pct 重算，对这一行会算出 8.36 并误报
    违例——而 9.55 才是交易所公布的真值。
    """
    _flags([{"dt": D[0], "symbol": "300029.SZ", "adj_prev_close": 7.96,
             "limit_pct": .05, "limit_up": 9.55, "limit_down": 6.37,
             "is_st": True}], warehouse)
    _limits([{"symbol": "300029.SZ", "dt": D[0], "pre_close": 7.96,
              "limit_up": 9.55, "limit_down": 6.37}], warehouse)
    assert _run(ctx, "A2.recompute.limit_up")["passed"] is True


def test_is_st_is_read_from_the_direct_truth_table(ctx, warehouse):
    """ST 取 stock_st 直取名单，不再由名称包含 'ST' 派生。"""
    _flags([{"dt": D[0], "symbol": "600001.SH", "is_st": True,
             "st_source": "stock_st"}], warehouse)
    _daily([{"symbol": "600001.SH", "dt": D[0], "close": 10.0}], warehouse)
    _stock_st([{"symbol": "600001.SH", "dt": D[0], "is_st": True}], warehouse)
    assert _run(ctx, "A2.recompute.is_st")["passed"] is True


def test_is_st_reports_a_row_the_direct_table_contradicts(ctx, warehouse):
    _flags([{"dt": D[0], "symbol": "600001.SH", "is_st": True,
             "st_source": "stock_st"}], warehouse)
    _daily([{"symbol": "600001.SH", "dt": D[0], "close": 10.0}], warehouse)
    _stock_st([{"symbol": "600001.SH", "dt": D[0], "is_st": False}], warehouse)
    assert _run(ctx, "A2.recompute.is_st")["violations"] == 1
