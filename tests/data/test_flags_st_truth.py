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

from qsys.data.pipeline.flags import build_daily_flags
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
    assert got["st_source"].to_list() == ["paid"]


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
