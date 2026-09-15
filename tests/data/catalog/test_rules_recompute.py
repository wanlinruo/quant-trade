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
    _flags([{"dt": D[0], "symbol": "600036.SH", "adj_prev_close": 10.0,
             "limit_pct": .10, "limit_up": 11.0}], warehouse)
    assert _run(ctx, "A2.recompute.limit_up")["passed"] is True


def test_limit_up_reports_wrong_st_band(ctx, warehouse):
    _flags([{"dt": D[0], "symbol": "600001.SH", "adj_prev_close": 10.0,
             "limit_pct": .05, "limit_up": 11.0}], warehouse)
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
