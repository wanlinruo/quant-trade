"""涨跌停与首板判定。"""

from datetime import date

import pyarrow as pa

from qsys.core.constraints import PriceLimitConstraint
from qsys.data import schema
from qsys.data.pipeline.flags import build_daily_flags, limit_prices
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]


def test_main_board_limits_are_ten_percent():
    up, down = limit_prices(10.00, "main")
    assert (up, down) == (11.00, 9.00)


def test_gem_limits_are_twenty_percent():
    assert limit_prices(10.00, "gem") == (12.00, 8.00)


def test_limits_are_rounded_to_cents():
    up, down = limit_prices(9.99, "main")
    assert (up, down) == (10.99, 8.99)


def test_unknown_board_falls_back_to_main():
    assert limit_prices(10.00, "unknown") == (11.00, 9.00)


def test_half_cent_rounds_up():
    """1.65 × 0.9 = 1.485，四舍五入是 1.49，不是 1.48。"""
    assert limit_prices(1.65, "main")[1] == 1.49


def seed(warehouse: Warehouse, rows, boards=None) -> None:
    """rows: [(symbol, dt, open, high, low, close)]"""
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
                "amount": [0.0] * len(rows),
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )
    uniq = sorted({r[0] for r in rows})
    boards = boards or {}
    warehouse.write(
        "instruments",
        pa.table(
            {
                "symbol": uniq,
                "name": ["x"] * len(uniq),
                "market": ["SH"] * len(uniq),
                "list_date": [None] * len(uniq),
                "delist_date": [None] * len(uniq),
                "board": [boards.get(s, "main") for s in uniq],
            },
            schema=schema.INSTRUMENTS,
        ),
    )


def flags_of(warehouse, symbol, dt):
    df = WarehouseQuery(warehouse).daily_flags(start=dt, end=dt)
    return df.filter(df["symbol"] == symbol).row(0, named=True)


def test_limit_up_is_detected(warehouse):
    seed(
        warehouse,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
        ],
    )
    build_daily_flags(warehouse, D[0], D[1])
    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["is_limit_up"] is True
    assert row["limit_up"] == 11.00


def test_broken_board_is_touched_but_not_closed_at_limit(warehouse):
    """炸板：盘中触及涨停但收盘没封住。"""
    seed(
        warehouse,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 10.5, 11.0, 10.2, 10.6),
        ],
    )
    build_daily_flags(warehouse, D[0], D[1])
    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["touched_limit_up"] is True
    assert row["is_limit_up"] is False
    assert row["is_broken_board"] is True


def test_limit_down_is_detected(warehouse):
    seed(
        warehouse,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 9.0, 9.0, 9.0, 9.0),
        ],
    )
    build_daily_flags(warehouse, D[0], D[1])
    assert flags_of(warehouse, "600036.SH", D[1])["is_limit_down"] is True


def test_first_board_requires_no_limit_up_the_day_before(warehouse):
    """首板：今日涨停且昨日未涨停。连板不是首板。"""
    seed(
        warehouse,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
            ("600036.SH", D[2], 12.1, 12.1, 12.1, 12.1),
        ],
    )
    build_daily_flags(warehouse, D[0], D[2])
    assert flags_of(warehouse, "600036.SH", D[1])["is_first_board"] is True
    assert flags_of(warehouse, "600036.SH", D[2])["is_first_board"] is False


def test_first_bar_has_no_prev_close_and_no_flags(warehouse):
    seed(warehouse, [("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0)])
    build_daily_flags(warehouse, D[0], D[0])
    row = flags_of(warehouse, "600036.SH", D[0])
    assert row["prev_close"] is None
    assert row["is_limit_up"] is False


def test_flags_are_computed_per_symbol(warehouse):
    seed(
        warehouse,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
            ("600519.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600519.SH", D[1], 10.1, 10.2, 10.0, 10.1),
        ],
    )
    build_daily_flags(warehouse, D[0], D[1])
    assert flags_of(warehouse, "600036.SH", D[1])["is_limit_up"] is True
    assert flags_of(warehouse, "600519.SH", D[1])["is_limit_up"] is False


def test_board_determines_the_limit(warehouse):
    """创业板 20%：涨 10% 不算涨停。"""
    seed(
        warehouse,
        [
            ("300001.SZ", D[0], 10.0, 10.0, 10.0, 10.0),
            ("300001.SZ", D[1], 11.0, 11.0, 11.0, 11.0),
        ],
        boards={"300001.SZ": "gem"},
    )
    build_daily_flags(warehouse, D[0], D[1])
    row = flags_of(warehouse, "300001.SZ", D[1])
    assert row["limit_up"] == 12.00
    assert row["is_limit_up"] is False


def test_vectorized_limits_match_the_scalar_helper(warehouse):
    """整表算的涨跌停价必须与逐笔约束用的那个函数完全一致。

    两者若分头演化，回测撮合与候选池筛选就会对同一天给出不同答案。
    """
    prices = [round(1.0 + i * 0.05, 2) for i in range(60)]
    seeds = []
    for i, p in enumerate(prices):
        seeds.append((f"6{i:05d}.SH", D[0], p, p, p, p))
        seeds.append((f"6{i:05d}.SH", D[1], p, p, p, p))
    seed(warehouse, seeds)
    build_daily_flags(warehouse, D[0], D[1])

    df = WarehouseQuery(warehouse).daily_flags(start=D[1], end=D[1])
    for row in df.iter_rows(named=True):
        expected = PriceLimitConstraint.limit_prices(row["prev_close"], 0.10)
        assert (row["limit_up"], row["limit_down"]) == expected, row["symbol"]


def test_rebuild_is_idempotent(warehouse):
    seed(
        warehouse,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
        ],
    )
    assert build_daily_flags(warehouse, D[0], D[1]) == 2
    assert build_daily_flags(warehouse, D[0], D[1]) == 2
    assert WarehouseQuery(warehouse).daily_flags().height == 2


def test_build_on_empty_warehouse_returns_zero(warehouse):
    assert build_daily_flags(warehouse, D[0], D[1]) == 0
