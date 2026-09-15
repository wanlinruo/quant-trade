"""B 类规则必须能报告系统性空值与到货异常。"""

from datetime import date, datetime
import polars as pl
import pytest
from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_complete  # noqa: F401
from qsys.data.query import WarehouseQuery

D = [date(2026, 9, day) for day in (10, 11)]
NOW = datetime(2026, 9, 15, 19, 30)

@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(WarehouseQuery(warehouse), warehouse, D[0], D[-1])

def _flags(rows, warehouse):
    frame = pl.DataFrame(rows)
    bools = {"is_limit_up", "is_limit_down", "touched_limit_up", "is_broken_board", "is_first_board", "is_st"}
    frame = frame.with_columns([pl.lit(None, dtype=pl.Boolean if n in bools else pl.Utf8 if n == "st_source" else pl.Float64).alias(n)
                                for n in schema.DAILY_FLAGS.names if n not in frame.columns])
    warehouse.write("daily_flags", frame.select(schema.DAILY_FLAGS.names).to_arrow().cast(schema.DAILY_FLAGS), year=2026)

def _run(cid, ctx):
    return C.run_checks(ctx, "complete", NOW, only=[C.CHECKS[cid]], artifacts=ctx.warehouse.root/"samples").row(0, named=True)

def test_null_rate_has_both_denominators(ctx, warehouse):
    rows = [{"dt": D[0], "symbol": f"{i:06d}.SZ", "is_st": True} for i in range(97)]
    rows += [{"dt": D[0], "symbol": "000999.SZ", "is_st": None}] * 3
    _flags(rows, warehouse)
    detail = _run("B3.null_rate", ctx)["detail"]
    assert "按行" in detail and "按标的" in detail

def test_zero_limit_up_is_failure(ctx, warehouse):
    warehouse.write("limit_list_daily", schema.LIMIT_LIST_DAILY.empty_table(), year=2026)
    _flags([{"dt": D[0], "symbol": "600036.SH", "is_limit_up": False}], warehouse)
    row = _run("B4.zero_limit_up", ctx)
    assert row["passed"] is False and "零涨停" in row["detail"]

def test_calendar_has_a_real_denominator(ctx, warehouse):
    _flags([{"dt": D[0], "symbol": "600036.SH"}], warehouse)
    assert _run("B1.calendar", ctx)["checked_rows"] > 0

def test_all_six_rules_are_registered_as_completeness():
    ids = {cid for cid in C.CHECKS if cid.startswith("B")}
    assert ids == {"B1.calendar", "B2.holes", "B3.null_rate", "B4.arrival", "B4.zero_limit_up", "B5.span"}
    assert all(C.CHECKS[cid].category is C.Category.COMPLETENESS for cid in ids)
