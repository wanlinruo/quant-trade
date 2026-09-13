"""涨跌停与首板判定。"""

from datetime import date

import polars as pl
import pyarrow as pa
import pytest

from qsys.core.constraints import PriceLimitConstraint
from qsys.data import schema
from qsys.data.pipeline.flags import build_daily_flags, limit_prices
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]


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


def seed(warehouse: Warehouse, write_daily_bars, rows, boards=None) -> None:
    """rows: [(symbol, dt, open, high, low, close)]

    `pre_close` 按同一只股票在 rows 里出现的顺序取前一行的 close——
    真实付费源就是这样给的，同一只股票没有除权事件时 pre_close 恒等于
    前一交易日收盘价。首次出现的股票没有「前一行」，记 null。

    `name` 统一给一个不含 "ST" 的名字：这些测试测的是涨跌停/首板力学，
    不是 ST 判定本身（ST 判定的用例在 tests/data/test_flags_st_truth.py）。
    """
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
        "amount": [0.0] * len(rows),
    }))
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


def test_limit_up_is_detected(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
        ],
    )
    build_daily_flags(warehouse, D[0], D[1])
    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["is_limit_up"] is True
    assert row["limit_up"] == 11.00


def test_broken_board_is_touched_but_not_closed_at_limit(warehouse, write_daily_bars):
    """炸板：盘中触及涨停但收盘没封住。"""
    seed(warehouse, write_daily_bars,
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


def test_limit_down_is_detected(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 9.0, 9.0, 9.0, 9.0),
        ],
    )
    build_daily_flags(warehouse, D[0], D[1])
    assert flags_of(warehouse, "600036.SH", D[1])["is_limit_down"] is True


def test_first_board_requires_no_limit_up_the_day_before(warehouse, write_daily_bars):
    """首板：今日涨停且昨日未涨停。连板不是首板。"""
    seed(warehouse, write_daily_bars,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
            ("600036.SH", D[2], 12.1, 12.1, 12.1, 12.1),
        ],
    )
    build_daily_flags(warehouse, D[0], D[2])
    assert flags_of(warehouse, "600036.SH", D[1])["is_first_board"] is True
    assert flags_of(warehouse, "600036.SH", D[2])["is_first_board"] is False


def test_first_bar_has_no_prev_close_and_no_flags(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars, [("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0)])
    build_daily_flags(warehouse, D[0], D[0])
    row = flags_of(warehouse, "600036.SH", D[0])
    assert row["prev_close"] is None
    assert row["is_limit_up"] is False


def test_flags_are_computed_per_symbol(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars,
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


def test_board_determines_the_limit(warehouse, write_daily_bars):
    """创业板 20%：涨 10% 不算涨停。"""
    seed(warehouse, write_daily_bars,
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


def test_vectorized_limits_match_the_scalar_helper(warehouse, write_daily_bars):
    """整表算的涨跌停价必须与逐笔约束用的那个函数完全一致。

    两者若分头演化，回测撮合与候选池筛选就会对同一天给出不同答案。
    """
    prices = [round(1.0 + i * 0.05, 2) for i in range(60)]
    seeds = []
    for i, p in enumerate(prices):
        seeds.append((f"6{i:05d}.SH", D[0], p, p, p, p))
        seeds.append((f"6{i:05d}.SH", D[1], p, p, p, p))
    seed(warehouse, write_daily_bars, seeds)
    build_daily_flags(warehouse, D[0], D[1])

    df = WarehouseQuery(warehouse).daily_flags(start=D[1], end=D[1])
    for row in df.iter_rows(named=True):
        expected = PriceLimitConstraint.limit_prices(row["prev_close"], 0.10)
        assert (row["limit_up"], row["limit_down"]) == expected, row["symbol"]


def test_rebuild_is_idempotent(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
        ],
    )
    assert build_daily_flags(warehouse, D[0], D[1]) == 2
    assert build_daily_flags(warehouse, D[0], D[1]) == 2
    assert WarehouseQuery(warehouse).daily_flags().height == 2


def test_build_on_empty_warehouse_returns_zero(warehouse, write_daily_bars):
    assert build_daily_flags(warehouse, D[0], D[1]) == 0


def test_partial_rebuild_keeps_the_rest_of_the_year(warehouse, write_daily_bars):
    """只重算一段区间，不能把同年其余日期的标记删掉。

    分区是整年覆盖写的，若不与既有数据合并，每日增量重算会把历史抹掉。
    """
    days = [date(2024, 1, d) for d in (2, 3, 4, 5, 8)]
    seed(warehouse, write_daily_bars, [("600001.SH", d, 10.0, 10.0, 10.0, 10.0) for d in days])

    assert build_daily_flags(warehouse, days[0], days[-1]) == 5
    build_daily_flags(warehouse, days[-2], days[-1])  # 只重算最后两天

    after = WarehouseQuery(warehouse).daily_flags()
    assert after.height == 5, "早先的标记被覆盖掉了"
    assert after["dt"].to_list() == days


def test_partial_rebuild_refreshes_the_rows_it_covers(warehouse, write_daily_bars):
    """重算区间内的行要用新值覆盖，不是保留旧值。"""
    days = [date(2024, 1, d) for d in (2, 3)]
    seed(warehouse, write_daily_bars, [("600001.SH", days[0], 10.0, 10.0, 10.0, 10.0),
                     ("600001.SH", days[1], 10.4, 10.4, 10.4, 10.4)])
    build_daily_flags(warehouse, days[0], days[-1])
    assert flags_of(warehouse, "600001.SH", days[1])["is_limit_up"] is False

    # 把第二天改成涨停后只重算那一天
    seed(warehouse, write_daily_bars, [("600001.SH", days[0], 10.0, 10.0, 10.0, 10.0),
                     ("600001.SH", days[1], 11.0, 11.0, 11.0, 11.0)])
    build_daily_flags(warehouse, days[1], days[1])
    assert flags_of(warehouse, "600001.SH", days[1])["is_limit_up"] is True


def test_rebuild_drops_rows_the_source_no_longer_has(warehouse, write_daily_bars):
    """重算区间内源不再产出的股-日，必须从表里消失，不能留旧值。

    合并逻辑只做「增/覆盖」，窗口内被源删掉的行会一直留着。生产上实测到
    10 行这样的残留：旧表 bars_daily 有、付费源没有的股-日，重算后仍带着
    旧的 limit_pct=0.1，而 is_st / st_source 是新 schema 的 null——
    一行里一半是旧真值源的断言、一半是新真值源的空缺。表在断言一件当前
    真值源根本支撑不了的事，属于「拿不到就记 null，不准填」的结构性违反。
    """
    days = [date(2024, 1, 2), date(2024, 1, 3)]
    seed(warehouse, write_daily_bars,
         [("600001.SH", days[0], 10.0, 10.0, 10.0, 10.0),
          ("600001.SH", days[1], 10.0, 10.0, 10.0, 10.0),
          ("600002.SH", days[0], 20.0, 20.0, 20.0, 20.0),
          ("600002.SH", days[1], 20.0, 20.0, 20.0, 20.0)])
    assert build_daily_flags(warehouse, days[0], days[1]) == 4

    # 源里去掉 600002.SH 的第二天，再按同一区间整体重算
    seed(warehouse, write_daily_bars,
         [("600001.SH", days[0], 10.0, 10.0, 10.0, 10.0),
          ("600001.SH", days[1], 10.0, 10.0, 10.0, 10.0),
          ("600002.SH", days[0], 20.0, 20.0, 20.0, 20.0)])
    build_daily_flags(warehouse, days[0], days[1])

    after = WarehouseQuery(warehouse).daily_flags()
    assert after.height == 3, "源已删除的股-日仍留在表里"
    assert (after["symbol"] == "600002.SH").sum() == 1


def test_partial_rebuild_does_not_touch_rows_outside_the_window(warehouse, write_daily_bars):
    """删除只限于重算窗口之内——窗口外的行即便源里没有也不能动。

    这一条守着上一条的修法别改过头：按 dt 范围删，不是按全年删。
    """
    days = [date(2024, 1, d) for d in (2, 3, 4)]
    seed(warehouse, write_daily_bars,
         [("600001.SH", d, 10.0, 10.0, 10.0, 10.0) for d in days])
    assert build_daily_flags(warehouse, days[0], days[-1]) == 3

    # 源只剩最后一天，但只重算最后一天：前两天必须原样保留
    seed(warehouse, write_daily_bars,
         [("600001.SH", days[-1], 10.0, 10.0, 10.0, 10.0)])
    build_daily_flags(warehouse, days[-1], days[-1])

    after = WarehouseQuery(warehouse).daily_flags()
    assert after["dt"].to_list() == days


def test_a_normal_stock_keeps_the_board_limit(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
        ],
    )
    build_daily_flags(warehouse, D[0], D[1])
    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["limit_pct"] == pytest.approx(0.10)
    assert row["is_limit_up"] is True



