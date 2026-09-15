"""A 类真实性规则必须能在构造的坏数据上报警。"""

from datetime import date, datetime

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_truth  # noqa: F401
from qsys.data.query import WarehouseQuery

D = [date(2026, 9, day) for day in (7, 8, 9)]
NOW = datetime(2026, 9, 15, 19, 30)


@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(WarehouseQuery(warehouse), warehouse, D[0], D[-1])


def _daily(rows, warehouse):
    frame = pl.DataFrame(rows)
    missing = [name for name in schema.TUSHARE_DAILY.names if name not in frame.columns]
    frame = frame.with_columns([
        pl.lit(None, dtype=pl.Utf8 if name == "name" else pl.Float64).alias(name)
        for name in missing
    ]).select(schema.TUSHARE_DAILY.names)
    warehouse.write("tushare_daily", frame.to_arrow().cast(schema.TUSHARE_DAILY), year=2026)


def _run(check_id, ctx, tmp_path):
    return C.run_checks(ctx, "truth-test", NOW, only=[C.CHECKS[check_id]],
                        artifacts=tmp_path).row(0, named=True)


def _flags(rows, warehouse):
    frame = pl.DataFrame(rows)
    missing = [name for name in schema.DAILY_FLAGS.names if name not in frame.columns]
    bools = {"is_limit_up", "is_limit_down", "touched_limit_up",
             "is_broken_board", "is_first_board", "is_st"}
    frame = frame.with_columns([
        pl.lit(None, dtype=(pl.Boolean if name in bools else
                           pl.Utf8 if name == "st_source" else pl.Float64)).alias(name)
        for name in missing
    ]).select(schema.DAILY_FLAGS.names)
    warehouse.write("daily_flags", frame.to_arrow().cast(schema.DAILY_FLAGS), year=2026)


def test_amount_dimension_accepts_correct_units(ctx, warehouse, tmp_path):
    _daily([{"symbol": "600036.SH", "dt": D[0], "close": 10.0,
             "volume": 1000.0, "amount": 1_000_000.0}], warehouse)
    assert _run("A1.dimension.amount_vs_volume_close", ctx, tmp_path)["passed"] is True


def test_amount_dimension_catches_lost_multiplier(ctx, warehouse, tmp_path):
    _daily([{"symbol": "600036.SH", "dt": D[0], "close": 10.0,
             "volume": 1000.0, "amount": 1000.0}], warehouse)
    assert _run("A1.dimension.amount_vs_volume_close", ctx, tmp_path)["violations"] == 1


def test_change_identity_catches_bad_pre_close(ctx, warehouse, tmp_path):
    _daily([{"symbol": "600036.SH", "dt": D[0], "close": 11.0,
             "pre_close": 10.0, "change": 5.0}], warehouse)
    assert _run("A3.change_identity", ctx, tmp_path)["violations"] == 1


def test_market_cap_identity_catches_bad_shares(ctx, warehouse, tmp_path):
    _daily([{"symbol": "600036.SH", "dt": D[0], "close": 10.0,
             "market_cap": 1e10, "total_shares": 5e8}], warehouse)
    assert _run("A3.market_cap_vs_shares", ctx, tmp_path)["violations"] == 1


def test_missed_exdiv_flags_gap_without_factor_jump(ctx, warehouse, tmp_path):
    _daily([
        {"symbol": "000430.SZ", "dt": D[0], "close": 10.0, "pre_close": 10.0, "factor": 2.0},
        {"symbol": "000430.SZ", "dt": D[1], "close": 8.0, "pre_close": 8.0, "factor": 2.0},
    ], warehouse)
    row = _run("A3.missed_exdiv", ctx, tmp_path)
    assert row["violations"] == 1 and row["sample_path"] is not None


def test_missed_exdiv_accepts_matching_jump(ctx, warehouse, tmp_path):
    _daily([
        {"symbol": "600036.SH", "dt": D[0], "close": 10.0, "pre_close": 10.0, "factor": 2.0},
        {"symbol": "600036.SH", "dt": D[1], "close": 8.0, "pre_close": 8.0, "factor": 2.5},
    ], warehouse)
    assert _run("A3.missed_exdiv", ctx, tmp_path)["violations"] == 0


def test_limit_list_allows_only_st_to_be_missing(ctx, warehouse, tmp_path):
    _flags([
        {"dt": D[0], "symbol": "600036.SH", "is_limit_up": True, "is_st": False},
        {"dt": D[0], "symbol": "600001.SH", "is_limit_up": True, "is_st": True},
    ], warehouse)
    listed = pl.DataFrame([{
        "symbol": "600036.SH", "dt": D[0], "limit_type": "U",
        "limit_times": 1, "open_times": 0, "first_time": "09:30",
        "fd_amount": 0.0, "limit_amount": 0.0, "float_mv": 0.0,
    }]).select(schema.LIMIT_LIST_DAILY.names)
    warehouse.write("limit_list_daily", listed.to_arrow().cast(schema.LIMIT_LIST_DAILY), year=2026)
    assert _run("A3.limit_list_vs_selfcalc", ctx, tmp_path)["passed"] is True


def test_limit_list_reports_non_st_gap(ctx, warehouse, tmp_path):
    _flags([{"dt": D[0], "symbol": "600002.SH",
             "is_limit_up": True, "is_st": False}], warehouse)
    empty = pl.from_arrow(schema.LIMIT_LIST_DAILY.empty_table())
    warehouse.write(
        "limit_list_daily",
        empty.to_arrow().cast(schema.LIMIT_LIST_DAILY),
        year=2026,
    )
    assert _run("A3.limit_list_vs_selfcalc", ctx, tmp_path)["violations"] == 1


def test_limit_price_rule_reports_direct_source_disagreement(ctx, warehouse, tmp_path):
    _flags([{"dt": D[0], "symbol": "600036.SH",
             "limit_up": 11.0, "limit_down": 9.0}], warehouse)
    direct = pl.DataFrame([{
        "symbol": "600036.SH", "dt": D[0], "pre_close": 10.0,
        "limit_up": 99.0, "limit_down": 9.0,
    }]).select(schema.PRICE_LIMITS_DAILY.names)
    warehouse.write("price_limits_daily", direct.to_arrow().cast(schema.PRICE_LIMITS_DAILY), year=2026)
    assert _run("A3.limit_price_vs_selfcalc", ctx, tmp_path)["violations"] == 1


def test_minute_rule_reports_daily_high_disagreement(ctx, warehouse, tmp_path):
    _daily([{"symbol": "600036.SH", "dt": D[0], "open": 10.0, "high": 12.0,
             "low": 9.0, "close": 11.0, "volume": 100.0, "amount": 110000.0}], warehouse)
    minute = pl.DataFrame({
        "symbol": ["600036.SH"], "dt": [datetime(2026, 9, 7, 9, 30)],
        "open": [10.0], "high": [10.0], "low": [10.0], "close": [10.0],
        "volume": [100], "amount": [100000.0],
    })
    warehouse.write("minute_bars", minute.to_arrow().cast(schema.MINUTE_BARS), year=2026)
    assert _run("A3.minute_vs_daily", ctx, tmp_path)["violations"] == 1


def test_truth_rules_are_registered():
    expected = {
        "A1.dimension.amount_vs_volume_close", "A3.change_identity",
        "A3.pct_change_identity", "A3.turnover_identity", "A3.market_cap_vs_shares",
        "A3.missed_exdiv", "A3.limit_list_vs_selfcalc",
        "A3.limit_price_vs_selfcalc", "A3.minute_vs_daily",
    }
    assert expected <= set(C.CHECKS)
    assert all(C.CHECKS[item].category is C.Category.TRUTHFULNESS for item in expected)


# --- D-13 的不变式固化成常驻规则 -------------------------------------------


def test_limit_pct_reproduces_the_price_accepts_a_gem_st_row(ctx, warehouse, tmp_path):
    """7.96 × 1.20 按整数分算得 9.55，档位与价格自洽。"""
    _flags([{"dt": D[0], "symbol": "300029.SZ", "adj_prev_close": 7.96,
             "limit_pct": .20, "limit_up": 9.55, "limit_down": 6.37}], warehouse)
    assert _run("A3.limit_pct_reproduces_price", ctx, tmp_path)["passed"] is True


def test_limit_pct_reproduces_the_price_reports_the_d13_contradiction(ctx, warehouse, tmp_path):
    """这正是 D-13：limit_up 是 20% 档的真值，limit_pct 却还写着 5%。

    换源当天全仓有 68,649 行处于这个状态，靠肉眼是发现不了的。
    """
    _flags([{"dt": D[0], "symbol": "300029.SZ", "adj_prev_close": 7.96,
             "limit_pct": .05, "limit_up": 9.55, "limit_down": 6.37}], warehouse)
    assert _run("A3.limit_pct_reproduces_price", ctx, tmp_path)["violations"] == 1


def test_limit_pct_reproduces_the_price_skips_unlimited_rows(ctx, warehouse, tmp_path):
    """新股无涨跌幅限制时 limit_pct 记 null，那是「没有档位」不是「档位错」。"""
    _flags([{"dt": D[0], "symbol": "301105.SZ", "adj_prev_close": 38.19,
             "limit_pct": None, "limit_up": 1000000.0, "limit_down": 0.01}], warehouse)
    row = _run("A3.limit_pct_reproduces_price", ctx, tmp_path)
    assert row["violations"] == 0 and row["checked_rows"] == 0
