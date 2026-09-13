"""市场生态因子：策略 S7 Gate 的全部输入。"""

from datetime import date

import polars as pl
import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]


def seed(warehouse: Warehouse, write_daily_bars, rows) -> None:
    """rows: [(symbol, dt, open, high, low, close, amount)]

    `pre_close` 取同一只股票前一行的 close（真实付费源同样如此，非除权日
    pre_close 恒等于前收）；`name` 给个不含 "ST" 的名字——这里测的是
    market_regime 的派生逻辑，不是 ST 判定本身。
    """
    syms = sorted({r[0] for r in rows})
    last_close: dict[str, float] = {}
    pre_closes: list[float | None] = []
    for r in rows:
        pre_closes.append(last_close.get(r[0]))
        last_close[r[0]] = r[5]
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "dt": [r[1] for r in rows],
        "name": ["平安银行"] * len(rows),
        "open": [r[2] for r in rows],
        "high": [r[3] for r in rows],
        "low": [r[4] for r in rows],
        "close": [r[5] for r in rows],
        "pre_close": pre_closes,
        "volume": [1000.0] * len(rows),
        "amount": [r[6] for r in rows],
    }))
    warehouse.write(
        "instruments",
        pa.table(
            {
                "symbol": syms,
                "name": ["x"] * len(syms),
                "market": ["SH"] * len(syms),
                "list_date": [None] * len(syms),
                "delist_date": [None] * len(syms),
                "board": ["main"] * len(syms),
            },
            schema=schema.INSTRUMENTS,
        ),
    )
    build_daily_flags(warehouse, min(r[1] for r in rows), max(r[1] for r in rows))


def regime_of(warehouse, dt):
    df = WarehouseQuery(warehouse).market_regime(dt, dt)
    return df.row(0, named=True)


def test_counts_limit_ups(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [
            ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),
            ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600002.SH", D[1], 10.1, 10.2, 10.0, 10.1, 1e8),
        ],
    )
    build_market_regime(warehouse, D[0], D[1])
    assert regime_of(warehouse, D[1])["limit_up_count"] == 1


def test_counts_broken_boards_and_rate(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [
            ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),  # 封住
            ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600002.SH", D[1], 10.5, 11.0, 10.2, 10.6, 1e8),  # 炸板
        ],
    )
    build_market_regime(warehouse, D[0], D[1])
    row = regime_of(warehouse, D[1])
    assert row["touched_limit_up_count"] == 2
    assert row["broken_board_count"] == 1
    assert row["broken_board_rate"] == pytest.approx(0.5)


def test_advance_ratio_and_median_return(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [
            ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600001.SH", D[1], 10.0, 10.0, 10.0, 10.5, 1e8),  # +5%
            ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600002.SH", D[1], 10.0, 10.0, 10.0, 9.5, 1e8),  # -5%
            ("600003.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600003.SH", D[1], 10.0, 10.0, 10.0, 10.2, 1e8),  # +2%
        ],
    )
    build_market_regime(warehouse, D[0], D[1])
    row = regime_of(warehouse, D[1])
    assert row["market_advance_ratio"] == pytest.approx(2 / 3)
    assert row["market_median_return_1d"] == pytest.approx(0.02)


def test_first_board_count(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [
            ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),  # 首板
            ("600001.SH", D[2], 12.1, 12.1, 12.1, 12.1, 1e8),  # 连板，不是首板
        ],
    )
    build_market_regime(warehouse, D[0], D[2])
    assert regime_of(warehouse, D[1])["first_board_count"] == 1
    assert regime_of(warehouse, D[2])["first_board_count"] == 0


def test_prior_limitup_next_day_return(warehouse, write_daily_bars):
    """昨日涨停股今日的溢价——策略 S7 Gate 的 heat 指标。"""
    seed(
        warehouse, write_daily_bars,
        [
            ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),  # 涨停
            ("600001.SH", D[2], 11.5, 11.6, 11.4, 11.55, 1e8),  # 次日 +5%
        ],
    )
    build_market_regime(warehouse, D[0], D[2])
    assert regime_of(warehouse, D[2])[
        "prior_limitup_nextday_return_mean"
    ] == pytest.approx(0.05)


def test_prior_limitup_return_ignores_stocks_that_did_not_limit_up(warehouse, write_daily_bars):
    """只统计昨日涨停的那些票，不是全市场。"""
    seed(
        warehouse, write_daily_bars,
        [
            ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),  # 涨停
            ("600001.SH", D[2], 11.5, 11.6, 11.4, 11.55, 1e8),  # +5%
            ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600002.SH", D[1], 10.0, 10.0, 10.0, 10.0, 1e8),  # 未涨停
            ("600002.SH", D[2], 10.0, 10.0, 10.0, 8.0, 1e8),  # -20%，不该计入
        ],
    )
    build_market_regime(warehouse, D[0], D[2])
    assert regime_of(warehouse, D[2])[
        "prior_limitup_nextday_return_mean"
    ] == pytest.approx(0.05)


def test_total_money_is_summed(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [
            ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 2e8),
        ],
    )
    build_market_regime(warehouse, D[0], D[0])
    assert regime_of(warehouse, D[0])["market_total_money"] == pytest.approx(3e8)


def test_rebuild_is_idempotent(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [
            ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),
        ],
    )
    assert build_market_regime(warehouse, D[0], D[1]) == 2
    assert build_market_regime(warehouse, D[0], D[1]) == 2
    assert WarehouseQuery(warehouse).market_regime().height == 2


def test_build_on_empty_warehouse_returns_zero(warehouse, write_daily_bars):
    assert build_market_regime(warehouse, D[0], D[1]) == 0


def test_partial_rebuild_keeps_the_rest_of_the_year(warehouse, write_daily_bars):
    """生态因子同理：只重算一段不能抹掉同年其余日期。"""
    rows = []
    for i, d in enumerate(D):
        rows.append(("600001.SH", d, 10.0, 10.0, 10.0, 10.0, 1e8))
    seed(warehouse, write_daily_bars, rows)
    assert build_market_regime(warehouse, D[0], D[-1]) == 3
    build_market_regime(warehouse, D[-1], D[-1])
    after = WarehouseQuery(warehouse).market_regime()
    assert after.height == 3, "早先的生态因子被覆盖掉了"


def seed_index(warehouse, closes, start=date(2024, 1, 2)):
    """连续 len(closes) 个交易日的沪深300 收盘价。"""
    from datetime import timedelta

    days = [start + timedelta(days=i) for i in range(len(closes))]
    n = len(closes)
    warehouse.write(
        "index_bars",
        pa.table(
            {
                "symbol": ["000300.SH"] * n,
                "dt": days,
                "open": list(closes),
                "high": list(closes),
                "low": list(closes),
                "close": list(closes),
                "volume": [1000] * n,
                "amount": [1e10] * n,
            },
            schema=schema.INDEX_BARS,
        ),
        year=2024,
    )
    return days


def test_index_slope_is_null_without_index_data(warehouse, write_daily_bars):
    """没有指数数据时留空，不能编——S6 Gate 会据此走 UNKNOWN_ALLOW。"""
    seed(warehouse, write_daily_bars, [("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8)])
    build_market_regime(warehouse, D[0], D[0])
    assert regime_of(warehouse, D[0])["index_ma20_slope_5d"] is None


def test_index_slope_needs_at_least_25_sessions(warehouse, write_daily_bars):
    """MA20 与「5 日前的 MA20」合起来要 25 根，不足则留空。"""
    days = seed_index(warehouse, [3800.0] * 24)
    seed(warehouse, write_daily_bars, [("600001.SH", d, 10.0, 10.0, 10.0, 10.0, 1e8) for d in days])
    build_market_regime(warehouse, days[0], days[-1])
    assert regime_of(warehouse, days[-1])["index_ma20_slope_5d"] is None


def test_index_slope_is_zero_on_a_flat_index(warehouse, write_daily_bars):
    days = seed_index(warehouse, [3800.0] * 30)
    seed(warehouse, write_daily_bars, [("600001.SH", d, 10.0, 10.0, 10.0, 10.0, 1e8) for d in days])
    build_market_regime(warehouse, days[0], days[-1])
    assert regime_of(warehouse, days[-1])["index_ma20_slope_5d"] == pytest.approx(0.0)


def test_index_slope_is_positive_on_a_rising_index(warehouse, write_daily_bars):
    days = seed_index(warehouse, [3800.0 + i * 10 for i in range(30)])
    seed(warehouse, write_daily_bars, [("600001.SH", d, 10.0, 10.0, 10.0, 10.0, 1e8) for d in days])
    build_market_regime(warehouse, days[0], days[-1])
    row = regime_of(warehouse, days[-1])
    # 最近 20 根均值 = closes[10..29] = 3995；5 根之前的 20 根均值 = closes[5..24] = 3945
    assert row["index_ma20_slope_5d"] == pytest.approx(50.0 / 3945.0, rel=1e-9)


def test_star_and_bse_are_excluded_from_market_breadth(warehouse, write_daily_bars):
    """生态因子的股票池排除科创板与北交所（原策略 filter_kcbj_stock）。

    科创板涨跌幅是 20%，收益分布与主板不同；market_median_return_1d 的
    判定阈值只有 0.00426，混进去足以翻转 Gate 判定。
    """
    rows = []
    for symbol in ("600001.SH", "300001.SZ", "688001.SH", "430001.BJ", "830001.BJ"):
        rows.append((symbol, D[0], 10.0, 10.0, 10.0, 10.0, 1e8))
        rows.append((symbol, D[1], 10.0, 10.0, 10.0, 10.0, 1e8))
    seed(warehouse, write_daily_bars, rows)
    build_market_regime(warehouse, D[0], D[1])
    # 只剩主板 600001 与创业板 300001
    assert regime_of(warehouse, D[1])["valid_stock_count"] == 2


def test_b_shares_stay_in_the_breadth_universe(warehouse, write_daily_bars):
    """filter_kcbj_stock 只排 4/8/68 开头，B 股（900/200）不在其列。

    照抄原策略的口径，不自作主张多排。
    """
    rows = []
    for symbol in ("600001.SH", "900001.SH"):
        rows.append((symbol, D[0], 10.0, 10.0, 10.0, 10.0, 1e8))
        rows.append((symbol, D[1], 10.0, 10.0, 10.0, 10.0, 1e8))
    seed(warehouse, write_daily_bars, rows)
    build_market_regime(warehouse, D[0], D[1])
    assert regime_of(warehouse, D[1])["valid_stock_count"] == 2


def test_the_ex_dividend_drop_is_not_counted_as_a_fall(warehouse, write_daily_bars):
    """除权日的原始价大跌不是真跌，不修正会把广度类因子系统性压低。

    A 股 10 送 1：前收 11.00，除权参考价 10.00，当日平收 10.00。
    原始收益 -9.1%，复权后 0%。
    """
    seed(warehouse, write_daily_bars, [
        ("600001.SH", D[0], 11.0, 11.0, 11.0, 11.0, 1e8),
        ("600001.SH", D[1], 10.0, 10.0, 10.0, 10.0, 1e8),
        # 陪跑的一只，保证分母不为空
        ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600002.SH", D[1], 10.1, 10.1, 10.1, 10.1, 1e8),
    ])
    warehouse.write(
        "adj_factors",
        pa.table(
            {
                "symbol": ["600001.SH"] * 2 + ["600002.SH"] * 2,
                "dt": [D[0], D[1], D[0], D[1]],
                "cum_factor": [1.0, 1.1, 1.0, 1.0],
                "cum_share_factor": [1.0, 1.1, 1.0, 1.0],
            },
            schema=schema.ADJ_FACTORS,
        ),
    )
    build_daily_flags(warehouse, D[0], D[1])
    build_market_regime(warehouse, D[0], D[1])

    row = (
        WarehouseQuery(warehouse)
        .market_regime()
        .filter(pl_col_eq("dt", D[1]))
        .to_dicts()[0]
    )
    # 除权那只算 0%（不涨不跌），另一只 +1% -> 上涨 1 家、下跌 0 家
    assert row["market_advance_ratio"] == pytest.approx(1.0)
    assert row["market_median_return_1d"] == pytest.approx(0.005)


def test_without_factors_the_raw_return_is_used(warehouse, write_daily_bars):
    """仓库还没有复权因子时不能崩，退化成原始收益。"""
    seed(warehouse, write_daily_bars, [
        ("600001.SH", D[0], 11.0, 11.0, 11.0, 11.0, 1e8),
        ("600001.SH", D[1], 10.0, 10.0, 10.0, 10.0, 1e8),
    ])
    build_daily_flags(warehouse, D[0], D[1])
    assert build_market_regime(warehouse, D[0], D[1]) > 0
    row = (
        WarehouseQuery(warehouse).market_regime()
        .filter(pl_col_eq("dt", D[1])).to_dicts()[0]
    )
    assert row["market_advance_ratio"] == pytest.approx(0.0)


def pl_col_eq(name, value):
    import polars as pl

    return pl.col(name) == value


def test_board_filter_keeps_beijing_out_of_the_breadth_factor(warehouse, write_daily_bars):
    """广度因子的口径必须与策略一致——策略排北交所。

    付费源比旧表多 343 只北交所，不过滤会让 market_advance_ratio
    在换源当天静默跳变，而没有任何地方报错。
    """
    day = date(2026, 9, 9)
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ", "920268.BJ"], "dt": [day, day],
        "name": ["平安银行", "京证股份"],
        "open": [10.0, 5.0], "high": [10.0, 5.0], "low": [10.0, 5.0],
        "close": [10.0, 5.0], "pre_close": [10.0, 5.0],
        "volume": [100.0, 100.0], "amount": [1e5, 5e4],
    }))
    boards = {"main", "gem", "star"}
    build_daily_flags(warehouse, day, day, boards=boards)
    build_market_regime(warehouse, day, day, boards=boards)
    got = WarehouseQuery(warehouse).market_regime(day, day)
    assert got.height == 1


def test_a_stock_that_did_not_move_is_flat_not_down(warehouse, write_daily_bars):
    """浮点噪声不能算成下跌。

    close/prev_close 乘上复权因子后，没动的票会算出 -2.22e-16 这种数。
    advance_ratio 的分母不含持平，错分一只就直接改比值——实测
    2026-04-30 的 002841.SZ 就因此被判成下跌。
    """
    seed(warehouse, write_daily_bars, [
        ("600001.SH", D[0], 8.99, 8.99, 8.99, 8.99, 1e8),
        ("600001.SH", D[1], 8.99, 8.99, 8.99, 8.99, 1e8),
        ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600002.SH", D[1], 10.1, 10.1, 10.1, 10.1, 1e8),
    ])
    warehouse.write(
        "adj_factors",
        pa.table(
            {
                "symbol": ["600001.SH"] * 2 + ["600002.SH"] * 2,
                "dt": [D[0], D[1], D[0], D[1]],
                # 因子恒定，但 cum_factor(t)/cum_factor(t-1) 的浮点除法
                # 未必给出精确的 1.0
                "cum_factor": [1.07, 1.07, 1.0, 1.0],
                "cum_share_factor": [1.0, 1.0, 1.0, 1.0],
            },
            schema=schema.ADJ_FACTORS,
        ),
    )
    build_daily_flags(warehouse, D[0], D[1])
    build_market_regime(warehouse, D[0], D[1])

    row = (WarehouseQuery(warehouse).market_regime()
           .filter(pl_col_eq("dt", D[1])).to_dicts()[0])
    # 没动的那只算持平，不进分母 -> 只剩上涨的那只
    assert row["market_advance_ratio"] == pytest.approx(1.0)
