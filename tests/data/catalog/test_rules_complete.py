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
    assert row["severity"] == "warning"
    assert row["known_issue"] == "D-12"


def test_today_zero_limit_is_na_before_release_sla(ctx, warehouse, monkeypatch):
    today = date(2026, 9, 15)
    current = C.CheckContext(ctx.query, warehouse, today, today)
    warehouse.write("limit_list_daily", schema.LIMIT_LIST_DAILY.empty_table(), year=2026)
    _flags([{"dt": today, "symbol": "600036.SH", "is_limit_up": False}], warehouse)
    monkeypatch.setattr(rules_complete, "_now", lambda: datetime(2026, 9, 15, 18, 59))
    row = _run("B4.zero_limit_up", current)
    assert row["passed"] is True
    assert row["checked_rows"] == 0
    assert "19:00" in row["detail"] and "不适用" in row["detail"]


def test_today_zero_limit_is_failure_after_release_sla(ctx, warehouse, monkeypatch):
    today = date(2026, 9, 15)
    current = C.CheckContext(ctx.query, warehouse, today, today)
    warehouse.write("limit_list_daily", schema.LIMIT_LIST_DAILY.empty_table(), year=2026)
    _flags([{"dt": today, "symbol": "600036.SH", "is_limit_up": False}], warehouse)
    monkeypatch.setattr(rules_complete, "_now", lambda: datetime(2026, 9, 15, 19, 0))
    row = _run("B4.zero_limit_up", current)
    assert row["passed"] is False
    assert row["known_issue"] == "D-12"

def test_calendar_has_a_real_denominator(ctx, warehouse):
    _flags([{"dt": D[0], "symbol": "600036.SH"}], warehouse)
    assert _run("B1.calendar", ctx)["checked_rows"] > 0

def test_all_six_rules_are_registered_as_completeness():
    ids = {cid for cid in C.CHECKS if cid.startswith("B")}
    assert ids == {"B1.calendar", "B2.holes", "B3.null_rate", "B4.arrival", "B4.zero_limit_up", "B5.span"}
    assert all(C.CHECKS[cid].category is C.Category.COMPLETENESS for cid in ids)


# --- D-7：竞价 tick 到货 ------------------------------------------------------

BOARD = "600036.SH"


def _auction(warehouse, symbol, day):
    frame = pl.DataFrame({
        "symbol": [symbol], "dt": [day], "ts": [datetime(day.year, day.month, day.day, 9, 15, 3)],
        "bid1": [41.07], "ask1": [41.07], "bid_vol1": [100], "ask_vol1": [100],
        "last_price": [0.0], "volume": [0], "amount": [0.0],
    })
    warehouse.write("auction_ticks", frame.to_arrow().cast(schema.AUCTION_TICKS), year=day.year)


def _board_then_next_session(warehouse):
    """D[0] 首板，D[1] 是它的次日——竞价 tick 应在 D[1] 到货。"""
    _flags([{"dt": D[0], "symbol": BOARD, "is_first_board": True},
            {"dt": D[1], "symbol": BOARD, "is_first_board": False}], warehouse)


def _arrival_misses(row, warehouse):
    if row["sample_path"] is None:
        return pl.DataFrame()
    return pl.read_parquet(row["sample_path"]).filter(pl.col("dataset") == "auction_ticks")


def test_arrival_reports_first_board_without_next_day_auction(ctx, warehouse):
    _board_then_next_session(warehouse)
    row = _run("B4.arrival", ctx)
    assert row["passed"] is False
    missed = _arrival_misses(row, warehouse)
    assert missed.select("symbol", "dt").rows() == [(BOARD, D[1])]


def test_arrival_passes_when_auction_arrived(ctx, warehouse):
    _board_then_next_session(warehouse)
    _auction(warehouse, BOARD, D[1])
    row = _run("B4.arrival", ctx)
    assert row["passed"] is True
    assert row["checked_rows"] > 0


def test_arrival_does_not_expect_auction_for_suspended_candidate(ctx, warehouse):
    _board_then_next_session(warehouse)
    suspended = pl.DataFrame({"symbol": [BOARD], "dt": [D[1]], "suspend_type": ["S"],
                              "suspend_timing": [None]}, schema_overrides={"suspend_timing": pl.Utf8})
    warehouse.write("suspend_daily", suspended.to_arrow().cast(schema.SUSPEND_DAILY), year=2026)
    assert _run("B4.arrival", ctx)["passed"] is True


def test_arrival_waits_for_todays_auction_archive(warehouse, monkeypatch):
    today = D[1]
    current = C.CheckContext(WarehouseQuery(warehouse), warehouse, today, today)
    _board_then_next_session(warehouse)
    monkeypatch.setattr(rules_complete, "_now", lambda: datetime(2026, 9, 11, 15, 59))
    assert _arrival_misses(_run("B4.arrival", current), warehouse).height == 0
    monkeypatch.setattr(rules_complete, "_now", lambda: datetime(2026, 9, 11, 16, 0))
    assert _arrival_misses(_run("B4.arrival", current), warehouse).height == 1


def _write(dataset, arrow_schema, rows, warehouse):
    frame = pl.DataFrame(rows)
    frame = frame.with_columns([pl.lit(None).alias(n) for n in arrow_schema.names
                                if n not in frame.columns])
    warehouse.write(dataset, frame.select(arrow_schema.names).to_arrow().cast(arrow_schema),
                    year=2026)


def test_holes_explained_by_direct_suspension_are_not_reported(warehouse):
    """2026 年生产 1593 个「空洞」全部是 suspend_d 有记录的停牌日，不是缺数据。"""
    days = [date(2026, 9, d) for d in (7, 8, 9, 10)]
    daily = [{"symbol": s, "dt": d, "close": 10.0} for s in ("000001.SZ", "000002.SZ")
             for d in days]
    # 000001 停牌 09-08（有直取记录）；000002 缺 09-09 却没有停牌记录
    daily = [r for r in daily if (r["symbol"], r["dt"]) not in {
        ("000001.SZ", days[1]), ("000002.SZ", days[2])}]
    _write("tushare_daily", schema.TUSHARE_DAILY, daily, warehouse)
    _write("suspend_daily", schema.SUSPEND_DAILY, [
        {"symbol": "000001.SZ", "dt": days[1], "suspend_type": "S"},
        {"symbol": "000002.SZ", "dt": days[2], "suspend_type": "R"},   # 复牌记录不能解释缺 bar
    ], warehouse)
    result = C.CHECKS["B2.holes"].run(C.CheckContext(WarehouseQuery(warehouse), warehouse,
                                                    days[0], days[-1]))
    assert result.violations == 1
    assert result.sample.to_dicts() == [{"symbol": "000002.SZ", "dt": days[2]}]
    assert "停牌" in result.detail
