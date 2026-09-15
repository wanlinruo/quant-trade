"""ST 由真值直取，不再从价格反推。"""

import pyarrow as pa

from qsys.data import schema


def test_daily_flags_has_truth_columns_not_inference_audit_columns():
    """反推没了，反推的审计列也不该留。

    留着它们会让读代码的人以为启发式还在工作，也会让下游继续引用
    一个永远为 False 的列。
    """
    names = set(schema.DAILY_FLAGS.names)
    assert "is_st" in names
    assert "st_source" in names
    assert "st_inferred_raw" not in names
    assert "st_inference_rejected_by_price_evidence" not in names
    assert "st_inferred_final" not in names
    assert schema.DAILY_FLAGS.field("is_st").type == pa.bool_()
    assert schema.DAILY_FLAGS.field("st_source").type == pa.string()


def test_flag_columns_match_the_schema():
    """列清单与 schema 必须一致，否则 cast 会在落库时才炸。"""
    from qsys.data.pipeline.flags import _FLAG_COLUMNS

    assert _FLAG_COLUMNS == list(schema.DAILY_FLAGS.names)


from datetime import date

import polars as pl

from qsys.data.pipeline.flags import _assert_name_coverage, build_daily_flags, resolve_names
from qsys.data.query import WarehouseQuery

DAYS = [date(2026, 4, 29), date(2026, 4, 30)]


def _paid(write_daily_bars, warehouse, rows):
    write_daily_bars(warehouse, pl.DataFrame(rows))


def test_st_is_read_from_the_name_column(warehouse, write_daily_bars):
    """名称含 ST 即为 ST。不看价格，不看历史贴板次数。"""
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": ["*ST美丽"],
        "open": [3.0], "high": [3.0], "low": [3.0], "close": [3.0],
        "pre_close": [3.0], "volume": [100.0], "amount": [3e4],
    })
    build_daily_flags(warehouse, DAYS[1], DAYS[1])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[1], DAYS[1])
    assert got["is_st"].to_list() == [True]
    # 来源标的是具体那个源，不是笼统的 "tushare"。名称现在有三个候选直取源
    # （tushare_daily / paid_daily / namechange），分不清来源就查不出
    # 2026-09-14 那种「某一个源整段失效」的退化。
    assert got["st_source"].to_list() == ["tushare_daily"]


def test_complete_stock_st_truth_wins_over_a_conflicting_legacy_name(
    warehouse, write_daily_bars
):
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": ["*ST旧源"],
        "open": [3.0], "high": [3.0], "low": [3.0], "close": [3.0],
        "pre_close": [3.0], "volume": [100.0], "amount": [3e4],
    })
    warehouse.write("stock_st_daily", pa.table({
        "symbol": ["000010.SZ"], "dt": [DAYS[1]], "is_st": [False],
        "name": [None], "st_type": [None], "st_type_name": [None],
    }, schema=schema.STOCK_ST_DAILY), year=2026)
    build_daily_flags(warehouse, DAYS[1], DAYS[1])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[1], DAYS[1])
    assert got["is_st"].to_list() == [False]
    assert got["st_source"].to_list() == ["stock_st"]


def test_a_plain_name_is_not_st(warehouse, write_daily_bars):
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000010.SZ"], "dt": [DAYS[0]], "name": ["美丽生态"],
        "open": [3.0], "high": [3.0], "low": [3.0], "close": [3.0],
        "pre_close": [3.0], "volume": [100.0], "amount": [3e4],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[0])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[0])
    assert got["is_st"].to_list() == [False]


def test_st_status_is_per_day_not_carried_across_the_transition(warehouse, write_daily_bars):
    """同一只票跨 ST 生效日，两天的判定必须各自独立。

    000010.SZ 实测 2026-04-30 由「美丽生态」变为「*ST美丽」。
    """
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000010.SZ", "000010.SZ"], "dt": DAYS,
        "name": ["美丽生态", "*ST美丽"],
        "open": [3.0, 3.0], "high": [3.0, 3.0], "low": [3.0, 3.0],
        "close": [3.0, 3.0], "pre_close": [3.0, 3.0],
        "volume": [100.0, 100.0], "amount": [3e4, 3e4],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[1])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[1]).sort("dt")
    assert got["is_st"].to_list() == [False, True]


def test_a_null_name_yields_null_st_not_false(warehouse, write_daily_bars):
    """拿不到名称就记 null，不能默认成「不是 ST」。

    默认 False 会让一只 ST 股按 10% 档算涨停价，首板判定直接错。
    """
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000010.SZ"], "dt": [DAYS[0]],
        "open": [3.0], "high": [3.0], "low": [3.0], "close": [3.0],
        "pre_close": [3.0], "volume": [100.0], "amount": [3e4],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[0])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[0])
    assert got["is_st"].to_list() == [None]
    assert got["limit_up"].to_list() == [None]


def test_name_history_steps_on_the_exact_effective_day():
    bars = pl.DataFrame({
        "symbol": ["000010.SZ", "000010.SZ"], "dt": DAYS, "name": [None, None]
    })
    history = pl.DataFrame({
        "symbol": ["000010.SZ", "000010.SZ"],
        "name": ["美丽生态", "*ST美丽"],
        "start_date": [date(2020, 7, 22), date(2026, 4, 30)],
        "end_date": [date(2026, 4, 29), None],
    })
    empty_paid = pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date, "name": pl.Utf8})
    got = resolve_names(bars, empty_paid, history).sort("dt")
    assert got["name"].to_list() == ["美丽生态", "*ST美丽"]


def test_name_history_gap_stays_unknown():
    bars = pl.DataFrame({
        "symbol": ["000010.SZ"], "dt": [DAYS[0]], "name": [None]
    })
    history = pl.DataFrame({
        "symbol": ["000010.SZ"], "name": ["*ST美丽"],
        "start_date": [DAYS[1]], "end_date": [None],
    })
    empty_paid = pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date, "name": pl.Utf8})
    assert resolve_names(bars, empty_paid, history)["name"].to_list() == [None]


def test_large_name_coverage_failure_is_explicit():
    import pytest

    bars = pl.DataFrame({"name": [None] * 100})
    with pytest.raises(RuntimeError, match="覆盖不足"):
        _assert_name_coverage(bars)


def test_limit_price_uses_the_direct_pre_close(warehouse, write_daily_bars):
    """涨跌停基准直取 pre_close，不再从 corporate_actions 自算。

    实测 pre_close 就是除权参考价（3980 个除权日，与因子折算值中位差
    0.000154），比事件表自算可靠。
    """
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000001.SZ"], "dt": [DAYS[0]], "name": ["平安银行"],
        "open": [10.94], "high": [12.03], "low": [10.90], "close": [12.03],
        "pre_close": [10.94], "volume": [100.0], "amount": [1e5],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[0])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[0])
    assert got["limit_up"].to_list() == [12.03]   # round(10.94 * 1.10, 2)
    assert got["adj_prev_close"].to_list() == [10.94]
    assert got["is_limit_up"].to_list() == [True]


def test_st_limit_is_five_percent(warehouse, write_daily_bars):
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": ["*ST美丽"],
        "open": [3.0], "high": [3.15], "low": [3.0], "close": [3.15],
        "pre_close": [3.0], "volume": [100.0], "amount": [3e4],
    })
    build_daily_flags(warehouse, DAYS[1], DAYS[1])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[1], DAYS[1])
    assert got["limit_pct"].to_list() == [0.05]
    assert got["limit_up"].to_list() == [3.15]
    assert got["is_limit_up"].to_list() == [True]


def test_a_symbol_missing_from_instruments_falls_back_to_its_code_prefix(
    warehouse, write_daily_bars
):
    """instruments 里没有的标的按号段判板块，不能默认成 main。

    北交所上限 30%，默认成 main 会按 10% 算涨停价——旧表不含北交所所以
    这条路从没走过，付费源有 343 只。
    """
    _paid(write_daily_bars, warehouse, {
        "symbol": ["920268.BJ"], "dt": [DAYS[0]], "name": ["京证股份"],
        "open": [5.0], "high": [6.5], "low": [5.0], "close": [6.5],
        "pre_close": [5.0], "volume": [100.0], "amount": [5e4],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[0])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[0])
    assert got["limit_pct"].to_list() == [0.30]
    assert got["limit_up"].to_list() == [6.5]      # round(5.0 * 1.30, 2)
    assert got["is_limit_up"].to_list() == [True]


def test_board_filter_keeps_beijing_out_of_daily_flags(warehouse, write_daily_bars):
    """付费源含北交所 343 只，旧表没有。不显式过滤会静默改变广度因子。"""
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000001.SZ", "920268.BJ"], "dt": [DAYS[0], DAYS[0]],
        "name": ["平安银行", "京证股份"],
        "open": [10.0, 5.0], "high": [10.0, 5.0], "low": [10.0, 5.0],
        "close": [10.0, 5.0], "pre_close": [10.0, 5.0],
        "volume": [100.0, 100.0], "amount": [1e5, 5e4],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[0], boards={"main", "gem", "star"})
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[0])
    assert got["symbol"].to_list() == ["000001.SZ"]


def test_ex_dividend_day_uses_pre_close_not_the_previous_row_close(
    warehouse, write_daily_bars
):
    """`prev_close`（昨日收盘）与 `adj_prev_close`（除权参考价，直取自
    `pre_close`）只在除权日不相等——回归测试必须造一个真的会让两者分叉
    的除权日，否则测不出「涨跌停算错了基准」这种 bug。

    此前删除 `_with_reference_price`（事件表自算除权价）时的测试都是
    单行序列：`prev_close` 在那里是 null，不是"一个合理但错误的数值"，
    分辨不出 `_compute` 里 `cents = (pl.col("adj_prev_close") * 100)...`
    这一行如果被误改成 `prev_close` 会不会被测出来。

    这里构造两天连续数据：第一天收盘 11.30；第二天 `pre_close` 给
    10.94（模拟除权，不等于第一天收盘）。若涨跌停基准用错成
    `prev_close`（11.30），limit_up 会是 12.43 且 12.03 摸不到涨停；
    用对 `adj_prev_close`（10.94），limit_up 是 12.03 且当日封住涨停。
    """
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000001.SZ", "000001.SZ"], "dt": DAYS,
        "name": ["平安银行", "平安银行"],
        "open": [11.30, 11.50], "high": [11.30, 12.03],
        "low": [11.30, 11.40], "close": [11.30, 12.03],
        "pre_close": [None, 10.94],
        "volume": [100.0, 100.0], "amount": [1e5, 1e5],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[1])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[1], DAYS[1])
    assert got["prev_close"].to_list() == [11.30]       # 昨日收盘，未变
    assert got["adj_prev_close"].to_list() == [10.94]   # 除权参考价，直取
    assert got["limit_up"].to_list() == [12.03]         # round(10.94 * 1.10, 2)
    assert got["is_limit_up"].to_list() == [True]       # 用对基准才封得住涨停


# --- 名称来源优先级（2026-09-14）---------------------------------------
#
# namechange 只记变更事件。2006 年股改 G 股摘牌后名称恢复原状的那次变更
# 没有进 namechange，于是大量标的的最后一个区间停在 2006-10-08 且无后继，
# 此后全部无区间覆盖。实测单日 96 只、2023-2026 全区间 114 只因此没有名称，
# 顺着 is_st → limit_pct → limit_up → fill_null(False) 一路下去，
# 静默丢掉 867 个首板（artifacts/第4段回归_付费源到Tushare换源_2026-09-14.md）。
#
# 修法：paid_daily.name 是逐日观测到的名称，与 namechange 一样是直取字段，
# 两个直取源各管一段。逐日观测比区间推算强，所以先取它。


def _history(symbol, name, start, end):
    return pl.DataFrame({"symbol": [symbol], "name": [name],
                         "start_date": [start], "end_date": [end]})


def test_paid_name_fills_the_gap_namechange_leaves():
    """namechange 区间断在 2006 之后无后继——付费源的逐日名称要补上。"""
    from qsys.data.pipeline.flags import resolve_names

    bars = pl.DataFrame({"symbol": ["000006.SZ"], "dt": [DAYS[0]], "name": [None]})
    paid = pl.DataFrame({"symbol": ["000006.SZ"], "dt": [DAYS[0]], "name": ["深振业A"]})
    history = _history("000006.SZ", "G深振业", date(2006, 1, 12), date(2006, 10, 8))

    got = resolve_names(bars, paid, history)
    assert got["name"].to_list() == ["深振业A"]
    assert got["name_source"].to_list() == ["paid_daily"]


def test_namechange_still_covers_what_paid_does_not():
    """付费源没有的股-日（增量日）仍走区间表——两个源各管一段。"""
    from qsys.data.pipeline.flags import resolve_names

    bars = pl.DataFrame({"symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": [None]})
    paid = pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date, "name": pl.Utf8})
    history = _history("000010.SZ", "*ST美丽", DAYS[0], None)

    got = resolve_names(bars, paid, history)
    assert got["name"].to_list() == ["*ST美丽"]
    assert got["name_source"].to_list() == ["namechange"]


def test_paid_name_wins_over_the_interval_when_both_exist():
    """逐日观测优先于区间推算——区间只记变更，漏一次就整段错。"""
    from qsys.data.pipeline.flags import resolve_names

    bars = pl.DataFrame({"symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": [None]})
    paid = pl.DataFrame({"symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": ["美丽生态"]})
    history = _history("000010.SZ", "*ST美丽", DAYS[0], None)

    got = resolve_names(bars, paid, history)
    assert got["name"].to_list() == ["美丽生态"]
    assert got["name_source"].to_list() == ["paid_daily"]


def test_paid_name_is_not_carried_to_days_it_does_not_cover():
    """付费源只对它实际有行的那一天生效，绝不向前后蔓延。"""
    from qsys.data.pipeline.flags import resolve_names

    bars = pl.DataFrame({"symbol": ["000010.SZ"] * 2, "dt": DAYS, "name": [None, None]})
    paid = pl.DataFrame({"symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": ["美丽生态"]})
    history = pl.DataFrame(schema={"symbol": pl.Utf8, "name": pl.Utf8,
                                   "start_date": pl.Date, "end_date": pl.Date})

    got = resolve_names(bars, paid, history).sort("dt")
    assert got["name"].to_list() == [None, "美丽生态"]
    assert got["name_source"].to_list() == [None, "paid_daily"]


def test_both_sources_missing_stays_null():
    """两个源都没有就记 null——不拿当前名称回填历史。"""
    from qsys.data.pipeline.flags import resolve_names

    bars = pl.DataFrame({"symbol": ["000010.SZ"], "dt": [DAYS[0]], "name": [None]})
    empty_paid = pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date, "name": pl.Utf8})
    empty_hist = pl.DataFrame(schema={"symbol": pl.Utf8, "name": pl.Utf8,
                                      "start_date": pl.Date, "end_date": pl.Date})

    got = resolve_names(bars, empty_paid, empty_hist)
    assert got["name"].to_list() == [None]
    assert got["name_source"].to_list() == [None]


def test_st_source_is_null_exactly_where_the_name_is_unknown(warehouse, write_daily_bars):
    """没有名称就没有 ST 真值，st_source 必须跟着为 null。

    st_source 恒为一个字面量时，表面上每行都有来源，实际有 2% 的行根本
    没有名称——这正是 2026-09-14 那次静默退化没被发现的原因之一。
    让它为 null，覆盖率就能直接从表里查出来。
    """
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000010.SZ", "000011.SZ"], "dt": [DAYS[1], DAYS[1]],
        "name": ["*ST美丽", None],
        "open": [3.0, 3.0], "high": [3.0, 3.0], "low": [3.0, 3.0],
        "close": [3.0, 3.0], "pre_close": [3.0, 3.0],
        "volume": [100.0, 100.0], "amount": [3e4, 3e4],
    })
    build_daily_flags(warehouse, DAYS[1], DAYS[1])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[1], DAYS[1]).sort("symbol")
    assert got["is_st"].to_list() == [True, None]
    assert got["st_source"].to_list() == ["tushare_daily", None]


def test_snapshot_name_applies_only_to_the_day_it_was_taken():
    """快照是某一天的观测，只对那一天有效——蔓延到别的日期就是结转。"""
    from qsys.data.pipeline.flags import resolve_names

    bars = pl.DataFrame({"symbol": ["000006.SZ"] * 2, "dt": DAYS, "name": [None, None]})
    empty_paid = pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date, "name": pl.Utf8})
    empty_hist = pl.DataFrame(schema={"symbol": pl.Utf8, "name": pl.Utf8,
                                      "start_date": pl.Date, "end_date": pl.Date})
    snaps = pl.DataFrame({"symbol": ["000006.SZ"], "dt": [DAYS[1]], "name": ["深振业A"]})

    got = resolve_names(bars, empty_paid, empty_hist, snaps).sort("dt")
    assert got["name"].to_list() == [None, "深振业A"]
    assert got["name_source"].to_list() == [None, "name_snapshot"]


def test_snapshot_fills_the_gap_namechange_leaves_on_incremental_days():
    """增量日 paid_daily 没有数据，快照要顶上 namechange 的 2006 断层。"""
    from qsys.data.pipeline.flags import resolve_names

    bars = pl.DataFrame({"symbol": ["000006.SZ"], "dt": [DAYS[1]], "name": [None]})
    empty_paid = pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date, "name": pl.Utf8})
    history = _history("000006.SZ", "G深振业", date(2006, 1, 12), date(2006, 10, 8))
    snaps = pl.DataFrame({"symbol": ["000006.SZ"], "dt": [DAYS[1]], "name": ["深振业A"]})

    got = resolve_names(bars, empty_paid, history, snaps)
    assert got["name"].to_list() == ["深振业A"]
    assert got["name_source"].to_list() == ["name_snapshot"]


def test_paid_daily_still_wins_over_the_snapshot():
    """历史区间以付费源的逐日名称为准，快照只补它够不到的增量日。"""
    from qsys.data.pipeline.flags import resolve_names

    bars = pl.DataFrame({"symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": [None]})
    paid = pl.DataFrame({"symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": ["美丽生态"]})
    empty_hist = pl.DataFrame(schema={"symbol": pl.Utf8, "name": pl.Utf8,
                                      "start_date": pl.Date, "end_date": pl.Date})
    snaps = pl.DataFrame({"symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": ["错的名字"]})

    got = resolve_names(bars, paid, empty_hist, snaps)
    assert got["name"].to_list() == ["美丽生态"]
    assert got["name_source"].to_list() == ["paid_daily"]


def test_snapshot_wins_over_the_namechange_interval():
    """逐日观测强于区间推算——区间只记变更事件，漏一次就整段错。"""
    from qsys.data.pipeline.flags import resolve_names

    bars = pl.DataFrame({"symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": [None]})
    empty_paid = pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date, "name": pl.Utf8})
    history = _history("000010.SZ", "旧名", DAYS[0], None)
    snaps = pl.DataFrame({"symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": ["*ST美丽"]})

    got = resolve_names(bars, empty_paid, history, snaps)
    assert got["name"].to_list() == ["*ST美丽"]
    assert got["name_source"].to_list() == ["name_snapshot"]
