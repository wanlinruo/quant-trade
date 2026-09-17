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
             "low": 9.5, "high": 10.5,
             "volume": 1000.0, "amount": 1_000_000.0}], warehouse)
    assert _run("A1.dimension.amount_vs_volume_close", ctx, tmp_path)["passed"] is True


def test_amount_dimension_catches_lost_multiplier(ctx, warehouse, tmp_path):
    _daily([{"symbol": "600036.SH", "dt": D[0], "close": 10.0,
             "low": 9.5, "high": 10.5,
             "volume": 1000.0, "amount": 1000.0}], warehouse)
    assert _run("A1.dimension.amount_vs_volume_close", ctx, tmp_path)["violations"] == 1


def test_amount_dimension_accepts_vwap_far_from_close(ctx, warehouse, tmp_path):
    _daily([{"symbol": "600036.SH", "dt": D[0], "close": 10.0,
             "low": 8.0, "high": 10.5,
             "volume": 1000.0, "amount": 850_000.0}], warehouse)
    assert _run("A1.dimension.amount_vs_volume_close", ctx, tmp_path)["passed"] is True


@pytest.mark.parametrize("field,actual,expected", [
    ("pct_change", 0.0599, (16.71 / 16.70 - 1) * 100),
    ("turnover_ratio", 0.2162, 11366.64 * 100 / 525859082 * 100),
])
def test_four_decimal_source_fields_accept_rounding(field, actual, expected):
    frame = pl.DataFrame({"actual": [actual], "expected": [expected]})
    bad, checked = rules_truth._relative_bad(
        frame, rules_truth._REL_TOL, rules_truth._FOUR_DECIMAL_ABS_TOL,
    )
    assert checked == 1 and bad.height == 0


def test_change_identity_catches_bad_pre_close(ctx, warehouse, tmp_path):
    _daily([{"symbol": "600036.SH", "dt": D[0], "close": 11.0,
             "pre_close": 10.0, "change": 5.0}], warehouse)
    assert _run("A3.change_identity", ctx, tmp_path)["violations"] == 1


def test_market_cap_identity_catches_bad_shares(ctx, warehouse, tmp_path):
    _daily([{"symbol": "600036.SH", "dt": D[0], "close": 10.0,
             "market_cap": 1e10, "total_shares": 5e8,
             "circulating_market_cap": 4e9, "circulating_shares": 4e8}], warehouse)
    assert _run("A3.market_cap_vs_shares", ctx, tmp_path)["violations"] == 1


def test_market_cap_rule_accepts_cross_listed_share_scope(ctx, warehouse, tmp_path):
    """全球总股本可大于 A 股市值采用的计价股本，不能误报 A+H/ADR。"""
    _daily([{
        "symbol": "688235.SH", "dt": D[0], "close": 253.56,
        "total_shares": 1_542_285_181.0,
        "market_cap": 372_550_332_528.0,
        "circulating_shares": 115_055_260.0,
        "circulating_market_cap": 29_173_421_868.0,
    }], warehouse)
    assert _run("A3.market_cap_vs_shares", ctx, tmp_path)["passed"] is True


def test_market_cap_rule_accepts_previous_session_share_on_change_day(
    ctx, warehouse, tmp_path,
):
    """daily_basic 的市值在股本变化日可能仍按上一交易日股本计算。"""
    _daily([
        {"symbol": "300803.SZ", "dt": D[0], "close": 80.0,
         "total_shares": 616_500_530.0, "market_cap": 49_320_042_400.0,
         "circulating_shares": 612_047_797.0,
         "circulating_market_cap": 48_963_823_760.0},
        {"symbol": "300803.SZ", "dt": D[1], "close": 77.15,
         "total_shares": 616_536_061.0,
         "market_cap": 616_500_530.0 * 77.15,
         "circulating_shares": 611_879_416.0,
         "circulating_market_cap": 612_047_797.0 * 77.15},
    ], warehouse)
    assert _run("A3.market_cap_vs_shares", ctx, tmp_path)["passed"] is True


def _stale_market_cap_rows(stale_sessions):
    """股本在第 1 个交易日切新值，此后 `stale_sessions` 天市值仍按旧股本计价。"""
    days = [date(2026, 8, 24 + i) for i in range(5)] + [date(2026, 8, 31)] + [date(2026, 9, 1 + i) for i in range(4)]
    old, new = (616_500_530.0, 612_047_797.0), (616_536_061.0, 611_879_416.0)
    rows = [{"symbol": "300803.SZ", "dt": days[0], "close": 80.0,
             "total_shares": old[0], "market_cap": old[0] * 80.0,
             "circulating_shares": old[1], "circulating_market_cap": old[1] * 80.0}]
    for day in days[1:1 + stale_sessions]:
        rows.append({"symbol": "300803.SZ", "dt": day, "close": 77.15,
                     "total_shares": new[0], "market_cap": old[0] * 77.15,
                     "circulating_shares": new[1], "circulating_market_cap": old[1] * 77.15})
    return rows


@pytest.mark.parametrize("stale_sessions,passed", [(5, True), (6, False)])
def test_market_cap_rule_tolerates_source_lag_up_to_the_window(
    warehouse, tmp_path, stale_sessions, passed,
):
    """生产 2026：daily_basic 市值滞后股本变化常达 3~5 个交易日；更久的仍要报。"""
    rows = _stale_market_cap_rows(stale_sessions)
    _daily(rows, warehouse)
    last = rows[-1]["dt"]
    ctx = C.CheckContext(WarehouseQuery(warehouse), warehouse, last, last)
    assert _run("A3.market_cap_vs_shares", ctx, tmp_path)["passed"] is passed


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
    # 另一只股票的记录证明接口当天已经到货；此时缺少 600002 才是真差异。
    listed = pl.DataFrame([{
        "symbol": "600003.SH", "dt": D[0], "limit_type": "D",
        "limit_times": 1, "open_times": 0, "first_time": "09:30",
        "fd_amount": 0.0, "limit_amount": 0.0, "float_mv": 0.0,
    }]).select(schema.LIMIT_LIST_DAILY.names)
    warehouse.write(
        "limit_list_daily",
        listed.to_arrow().cast(schema.LIMIT_LIST_DAILY),
        year=2026,
    )
    assert _run("A3.limit_list_vs_selfcalc", ctx, tmp_path)["violations"] == 1


def test_limit_list_comparison_is_not_applicable_before_source_arrives(
    ctx, warehouse, tmp_path,
):
    _flags([{"dt": D[0], "symbol": "600002.SH",
             "is_limit_up": True, "is_st": False}], warehouse)
    row = _run("A3.limit_list_vs_selfcalc", ctx, tmp_path)
    assert row["passed"] is True
    assert row["checked_rows"] == 0
    assert "不适用" in row["detail"]


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
