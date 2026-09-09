"""市场生态因子：策略 S7 Gate 的全部输入。"""

from datetime import date

import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]


def seed(warehouse: Warehouse, rows) -> None:
    """rows: [(symbol, dt, open, high, low, close, amount)]"""
    syms = sorted({r[0] for r in rows})
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": [r[0] for r in rows],
                "dt": [r[1] for r in rows],
                "open": [r[2] for r in rows],
                "high": [r[3] for r in rows],
                "low": [r[4] for r in rows],
                "close": [r[5] for r in rows],
                "volume": [1000] * len(rows),
                "amount": [r[6] for r in rows],
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )
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


def test_counts_limit_ups(warehouse):
    seed(
        warehouse,
        [
            ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),
            ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600002.SH", D[1], 10.1, 10.2, 10.0, 10.1, 1e8),
        ],
    )
    build_market_regime(warehouse, D[0], D[1])
    assert regime_of(warehouse, D[1])["limit_up_count"] == 1


def test_counts_broken_boards_and_rate(warehouse):
    seed(
        warehouse,
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


def test_advance_ratio_and_median_return(warehouse):
    seed(
        warehouse,
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


def test_first_board_count(warehouse):
    seed(
        warehouse,
        [
            ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),  # 首板
            ("600001.SH", D[2], 12.1, 12.1, 12.1, 12.1, 1e8),  # 连板，不是首板
        ],
    )
    build_market_regime(warehouse, D[0], D[2])
    assert regime_of(warehouse, D[1])["first_board_count"] == 1
    assert regime_of(warehouse, D[2])["first_board_count"] == 0


def test_prior_limitup_next_day_return(warehouse):
    """昨日涨停股今日的溢价——策略 S7 Gate 的 heat 指标。"""
    seed(
        warehouse,
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


def test_prior_limitup_return_ignores_stocks_that_did_not_limit_up(warehouse):
    """只统计昨日涨停的那些票，不是全市场。"""
    seed(
        warehouse,
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


def test_total_money_is_summed(warehouse):
    seed(
        warehouse,
        [
            ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 2e8),
        ],
    )
    build_market_regime(warehouse, D[0], D[0])
    assert regime_of(warehouse, D[0])["market_total_money"] == pytest.approx(3e8)


def test_rebuild_is_idempotent(warehouse):
    seed(
        warehouse,
        [
            ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
            ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),
        ],
    )
    assert build_market_regime(warehouse, D[0], D[1]) == 2
    assert build_market_regime(warehouse, D[0], D[1]) == 2
    assert WarehouseQuery(warehouse).market_regime().height == 2


def test_build_on_empty_warehouse_returns_zero(warehouse):
    assert build_market_regime(warehouse, D[0], D[1]) == 0


def test_partial_rebuild_keeps_the_rest_of_the_year(warehouse):
    """生态因子同理：只重算一段不能抹掉同年其余日期。"""
    rows = []
    for i, d in enumerate(D):
        rows.append(("600001.SH", d, 10.0, 10.0, 10.0, 10.0, 1e8))
    seed(warehouse, rows)
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


def test_index_slope_is_null_without_index_data(warehouse):
    """没有指数数据时留空，不能编——S6 Gate 会据此走 UNKNOWN_ALLOW。"""
    seed(warehouse, [("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8)])
    build_market_regime(warehouse, D[0], D[0])
    assert regime_of(warehouse, D[0])["index_ma20_slope_5d"] is None


def test_index_slope_needs_at_least_25_sessions(warehouse):
    """MA20 与「5 日前的 MA20」合起来要 25 根，不足则留空。"""
    days = seed_index(warehouse, [3800.0] * 24)
    seed(warehouse, [("600001.SH", d, 10.0, 10.0, 10.0, 10.0, 1e8) for d in days])
    build_market_regime(warehouse, days[0], days[-1])
    assert regime_of(warehouse, days[-1])["index_ma20_slope_5d"] is None


def test_index_slope_is_zero_on_a_flat_index(warehouse):
    days = seed_index(warehouse, [3800.0] * 30)
    seed(warehouse, [("600001.SH", d, 10.0, 10.0, 10.0, 10.0, 1e8) for d in days])
    build_market_regime(warehouse, days[0], days[-1])
    assert regime_of(warehouse, days[-1])["index_ma20_slope_5d"] == pytest.approx(0.0)


def test_index_slope_is_positive_on_a_rising_index(warehouse):
    days = seed_index(warehouse, [3800.0 + i * 10 for i in range(30)])
    seed(warehouse, [("600001.SH", d, 10.0, 10.0, 10.0, 10.0, 1e8) for d in days])
    build_market_regime(warehouse, days[0], days[-1])
    row = regime_of(warehouse, days[-1])
    # 最近 20 根均值 = closes[10..29] = 3995；5 根之前的 20 根均值 = closes[5..24] = 3945
    assert row["index_ma20_slope_5d"] == pytest.approx(50.0 / 3945.0, rel=1e-9)
