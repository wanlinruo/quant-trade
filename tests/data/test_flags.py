"""涨跌停与首板判定。"""

from datetime import date

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


def seed_actions(warehouse: Warehouse, rows) -> None:
    """rows: [(symbol, ex_date, cash, stock, rights, rights_price)]"""
    warehouse.write(
        "corporate_actions",
        pa.table(
            {
                "symbol": [r[0] for r in rows],
                "ex_date": [r[1] for r in rows],
                "cash_per_share": [r[2] for r in rows],
                "stock_per_share": [r[3] for r in rows],
                "rights_per_share": [r[4] for r in rows],
                "rights_price": [r[5] for r in rows],
            },
            schema=schema.CORPORATE_ACTIONS,
        ),
    )


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


def test_partial_rebuild_keeps_the_rest_of_the_year(warehouse):
    """只重算一段区间，不能把同年其余日期的标记删掉。

    分区是整年覆盖写的，若不与既有数据合并，每日增量重算会把历史抹掉。
    """
    days = [date(2024, 1, d) for d in (2, 3, 4, 5, 8)]
    seed(warehouse, [("600001.SH", d, 10.0, 10.0, 10.0, 10.0) for d in days])

    assert build_daily_flags(warehouse, days[0], days[-1]) == 5
    build_daily_flags(warehouse, days[-2], days[-1])  # 只重算最后两天

    after = WarehouseQuery(warehouse).daily_flags()
    assert after.height == 5, "早先的标记被覆盖掉了"
    assert after["dt"].to_list() == days


def test_partial_rebuild_refreshes_the_rows_it_covers(warehouse):
    """重算区间内的行要用新值覆盖，不是保留旧值。"""
    days = [date(2024, 1, d) for d in (2, 3)]
    # 10.4 而非 10.5：后者恰好是 5% 涨停价，会被 ST 反推判成涨停
    seed(warehouse, [("600001.SH", days[0], 10.0, 10.0, 10.0, 10.0),
                     ("600001.SH", days[1], 10.4, 10.4, 10.4, 10.4)])
    build_daily_flags(warehouse, days[0], days[-1])
    assert flags_of(warehouse, "600001.SH", days[1])["is_limit_up"] is False

    # 把第二天改成涨停后只重算那一天
    seed(warehouse, [("600001.SH", days[0], 10.0, 10.0, 10.0, 10.0),
                     ("600001.SH", days[1], 11.0, 11.0, 11.0, 11.0)])
    build_daily_flags(warehouse, days[1], days[1])
    assert flags_of(warehouse, "600001.SH", days[1])["is_limit_up"] is True


def test_one_five_percent_hit_alone_is_not_an_st_stock(warehouse):
    """单次命中多半是巧合，不能据此判 ST。

    一只 ¥10 的股当日高点落在 +5% 那一分上的概率约 1/10，全市场每天撞出
    十几个。只用「当日命中」这一条时，与聚宽对账近窗口 40 天多放行、
    0 天多拒掉——单边偏差。
    """
    seed(
        warehouse,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            # 前收 10.00 -> 5% 价 10.50，恰好摸到，但只此一次
            ("600036.SH", D[1], 10.2, 10.5, 10.1, 10.3),
        ],
    )
    build_daily_flags(warehouse, D[0], D[1])
    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["limit_pct"] == pytest.approx(0.10)
    assert row["touched_limit_up"] is False


def test_a_second_five_percent_hit_confirms_st(warehouse):
    """ST 是持续数月的状态，真 ST 会反复贴着 5% 板。"""
    seed(
        warehouse,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 10.2, 10.5, 10.1, 10.0),   # 第 1 次命中
            ("600036.SH", D[2], 10.2, 10.5, 10.1, 10.3),   # 第 2 次，确认
        ],
    )
    build_daily_flags(warehouse, D[0], D[2])
    assert flags_of(warehouse, "600036.SH", D[1])["limit_pct"] == pytest.approx(0.10)
    row = flags_of(warehouse, "600036.SH", D[2])
    assert row["touched_limit_up"] is True
    assert row["limit_pct"] == pytest.approx(0.05)


def test_a_five_percent_close_at_limit_is_a_limit_up(warehouse):
    """连续 5% 涨停时前收逐日抬高，5% 价也跟着走：10.00 -> 10.50 -> 11.03。"""
    seed(
        warehouse,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 10.5, 10.5, 10.5, 10.5),      # 第 1 次命中
            ("600036.SH", D[2], 11.03, 11.03, 11.03, 11.03),  # 第 2 次，确认
        ],
    )
    build_daily_flags(warehouse, D[0], D[2])
    row = flags_of(warehouse, "600036.SH", D[2])
    assert row["is_limit_up"] is True
    assert row["limit_up"] == pytest.approx(11.03)


def test_rising_above_five_percent_is_not_an_st_stock(warehouse):
    """涨过 5% 就不可能是 ST——按板块的 10% 判。"""
    seed(
        warehouse,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 10.5, 10.8, 10.4, 10.7),
        ],
    )
    build_daily_flags(warehouse, D[0], D[1])
    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["limit_pct"] == pytest.approx(0.10)
    assert row["touched_limit_up"] is False


def test_a_normal_stock_keeps_the_board_limit(warehouse):
    seed(
        warehouse,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
        ],
    )
    build_daily_flags(warehouse, D[0], D[1])
    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["limit_pct"] == pytest.approx(0.10)
    assert row["is_limit_up"] is True


def test_a_five_percent_first_board_counts(warehouse):
    """ST 股的首板同样是首板。头两天先把 ST 身份坐实。"""
    seed(
        warehouse,
        [
            ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
            # 前收 10.00 -> 5% 价 10.50：摸到但收在 10.30，第 1 次命中、未封板
            ("600036.SH", D[1], 10.2, 10.5, 10.1, 10.3),
            # 前收 10.30 -> 5% 价 10.82：第 2 次命中并封板，ST 身份确认
            ("600036.SH", D[2], 10.82, 10.82, 10.82, 10.82),
            # 前收 10.82 -> 5% 价 11.36：连板，不是首板
            ("600036.SH", D[3], 11.36, 11.36, 11.36, 11.36),
        ],
    )
    build_daily_flags(warehouse, D[0], D[3])
    assert flags_of(warehouse, "600036.SH", D[1])["is_limit_up"] is False
    assert flags_of(warehouse, "600036.SH", D[2])["is_first_board"] is True
    assert flags_of(warehouse, "600036.SH", D[3])["is_first_board"] is False


# --- 除权日的涨跌停按参考价算，不是按前收 ---


def test_cash_dividend_lowers_the_limit_up_price(warehouse):
    """000158.SZ 2026-06-09 的真实数据：前收 13.94、每股派 0.027。

    参考价 13.913，涨停价 15.30 —— 不是 13.94 x 1.1 = 15.33。
    当天它收在 15.30，是涨停。漏了这一步就漏判。
    """
    seed(warehouse, [
        ("000158.SZ", D[0], 14.11, 14.41, 13.74, 13.94),
        ("000158.SZ", D[1], 14.05, 15.30, 13.81, 15.30),
    ])
    seed_actions(warehouse, [("000158.SZ", D[1], 0.027, 0.0, 0.0, 0.0)])
    build_daily_flags(warehouse, D[0], D[1])

    row = flags_of(warehouse, "000158.SZ", D[1])
    assert row["prev_close"] == 13.94          # 原始前收不变
    assert row["adj_prev_close"] == pytest.approx(13.913)
    assert row["limit_up"] == 15.30
    assert row["is_limit_up"] is True


def test_without_the_dividend_the_same_bar_is_not_a_limit_up(warehouse):
    """同一根 bar，去掉分红记录就该判成没涨停——证明差异确实来自除权。"""
    seed(warehouse, [
        ("000158.SZ", D[0], 14.11, 14.41, 13.74, 13.94),
        ("000158.SZ", D[1], 14.05, 15.30, 13.81, 15.30),
    ])
    build_daily_flags(warehouse, D[0], D[1])

    row = flags_of(warehouse, "000158.SZ", D[1])
    assert row["limit_up"] == 15.33
    assert row["is_limit_up"] is False


def test_stock_dividend_divides_the_reference_price(warehouse):
    """688368.SH 2026-06-09：前收 201.80，10送4派1。

    参考价 (201.80 - 1.00) / 1.4 = 143.4286，科创板 20% 涨停价 172.12。
    按前收算会得到 242.16，差了 40%。
    """
    seed(warehouse, [
        ("688368.SH", D[0], 200.0, 205.0, 198.0, 201.80),
        ("688368.SH", D[1], 150.0, 172.43, 145.0, 172.43),
    ], boards={"688368.SH": "star"})
    seed_actions(warehouse, [("688368.SH", D[1], 1.0, 0.4, 0.0, 0.0)])
    build_daily_flags(warehouse, D[0], D[1])

    row = flags_of(warehouse, "688368.SH", D[1])
    assert row["adj_prev_close"] == pytest.approx(143.428571, abs=1e-6)
    assert row["limit_up"] == 172.12
    assert row["is_limit_up"] is True


def test_rights_issue_adds_the_subscription_proceeds(warehouse):
    """配股参考价 = (前收 + 配股价 x 配股比) / (1 + 配股比)。

    前收 10.00、每 10 股配 3 股、配股价 5.00：
    (10.00 + 5.00 x 0.3) / 1.3 = 8.846153，**先取整到分**得 8.85，
    涨停 8.85 x 1.1 = 9.735 -> 9.74。

    取整顺序不是细节：不先取整会得到 9.73，差一分就是涨停判定翻转。
    交易所是先公布取整到分的除权参考价，再由它算涨跌停——这条口径
    已用 000158.SZ / 003043.SZ / 603256.SH 三只的真实数据验证。
    """
    seed(warehouse, [
        ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.00),
        ("600036.SH", D[1], 9.0, 9.74, 8.8, 9.74),
    ])
    seed_actions(warehouse, [("600036.SH", D[1], 0.0, 0.0, 0.3, 5.0)])
    build_daily_flags(warehouse, D[0], D[1])

    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["adj_prev_close"] == pytest.approx(8.846153, abs=1e-6)
    assert row["limit_up"] == 9.74
    assert row["is_limit_up"] is True


def test_a_non_ex_date_reference_equals_the_previous_close(warehouse):
    seed(warehouse, [
        ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
        ("600036.SH", D[1], 10.5, 10.5, 10.5, 10.5),
    ])
    seed_actions(warehouse, [("600036.SH", D[3], 0.5, 0.0, 0.0, 0.0)])
    build_daily_flags(warehouse, D[0], D[1])

    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["adj_prev_close"] == row["prev_close"] == 10.0
    assert row["limit_up"] == 11.00


def test_two_dividend_rows_on_the_same_day_are_summed(warehouse):
    """部分数据源会把分次派息拆成多行，取合计而不是随机取一行。"""
    seed(warehouse, [
        ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.00),
        ("600036.SH", D[1], 9.5, 10.45, 9.4, 10.45),
    ])
    seed_actions(warehouse, [
        ("600036.SH", D[1], 0.30, 0.0, 0.0, 0.0),
        ("600036.SH", D[1], 0.20, 0.0, 0.0, 0.0),
    ])
    build_daily_flags(warehouse, D[0], D[1])

    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["adj_prev_close"] == pytest.approx(9.50)
    assert row["limit_up"] == 10.45
    assert row["is_limit_up"] is True


def test_flags_build_without_any_corporate_actions_table(warehouse):
    """仓库里还没有公司行动数据时不能崩——退化成用前收。"""
    seed(warehouse, [
        ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
        ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
    ])
    build_daily_flags(warehouse, D[0], D[1])
    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["adj_prev_close"] == 10.0
    assert row["is_limit_up"] is True


# --- ST 反推的制度硬否决 ---


def _st_series(warehouse, symbol, days, prices, boards=None):
    """prices: [(open, high, low, close)]，与 days 等长。"""
    seed(warehouse, [(symbol, d, *p) for d, p in zip(days, prices)], boards)


def test_repeated_five_percent_hits_infer_st(warehouse):
    """基线：连续贴 5% 板、从不越界，判成 ST。"""
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    # 10.00 -> 10.50 -> 11.03，每天恰好 +5%
    _st_series(warehouse, "600000.SH", days, [
        (10.00, 10.00, 10.00, 10.00),
        (10.50, 10.50, 10.50, 10.50),
        (11.03, 11.03, 11.03, 11.03),
    ])
    build_daily_flags(warehouse, days[0], days[-1])
    row = flags_of(warehouse, "600000.SH", days[-1])
    assert row["st_inferred_raw"] is True
    assert row["st_inference_rejected_by_price_evidence"] is False
    assert row["st_inferred_final"] is True
    assert row["limit_pct"] == 0.05


def test_hits_before_a_breach_do_not_count_towards_st(warehouse):
    """越界把计数清零：越界前的贴板不能证明「现在是 ST」。

    这是 2025-12-05 那 6 只的形态——早先贴过 5% 板，中间走出过 5% 区间，
    然后又贴了一次。旧规则只数 60 根内的命中，会把它们判成 ST。
    """
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
    _st_series(warehouse, "600000.SH", days, [
        (10.00, 10.00, 10.00, 10.00),
        (10.50, 10.50, 10.50, 10.50),   # 恰好 +5% -> 命中 1
        (11.50, 11.60, 11.40, 11.50),   # 高点 11.60 > 5% 涨停价 11.03 -> 越界
        (12.08, 12.08, 12.00, 12.08),   # 恰好 +5% -> 命中 2（但在越界之后只此一次）
    ])
    build_daily_flags(warehouse, days[0], days[-1])
    row = flags_of(warehouse, "600000.SH", days[-1])
    assert row["st_inferred_raw"] is True          # 60 根内确有 2 次命中
    assert row["st_inference_rejected_by_price_evidence"] is True
    assert row["st_inferred_final"] is False
    assert row["limit_pct"] == 0.10
    assert row["limit_up"] == 12.65                # 11.50 x 1.1，不是 12.08
    assert row["is_limit_up"] is False
    assert row["touched_limit_up"] is False


def test_two_hits_after_a_breach_re_establish_st(warehouse):
    """越界之后又攒够两次贴板 -> 期间被 ST 了，重新按 5% 档。

    ST 是阶跃状态：越界只证明那一天不是 ST。一律按窗口否决会把刚被 ST
    的股票连坐，因为它们被 ST 之前必然有越界。
    """
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4),
            date(2024, 1, 5), date(2024, 1, 8)]
    _st_series(warehouse, "600000.SH", days, [
        (10.00, 10.00, 10.00, 10.00),
        (10.50, 10.50, 10.50, 10.50),   # 命中 1
        (11.50, 11.60, 11.40, 11.50),   # 越界，计数清零
        (12.08, 12.08, 12.00, 12.08),   # 越界后命中 1
        (12.68, 12.68, 12.60, 12.68),   # 越界后命中 2 -> 重新认定 ST
    ])
    build_daily_flags(warehouse, days[0], days[-1])
    assert flags_of(warehouse, "600000.SH", days[3])["st_inferred_final"] is False
    row = flags_of(warehouse, "600000.SH", days[-1])
    assert row["st_inference_rejected_by_price_evidence"] is False
    assert row["st_inferred_final"] is True
    assert row["limit_pct"] == 0.05


def test_a_downside_breach_also_counts(warehouse):
    """跌破 5% 跌停同样是越界证据，不能只看上方。"""
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
    _st_series(warehouse, "600000.SH", days, [
        (10.00, 10.00, 10.00, 10.00),
        (10.50, 10.50, 10.50, 10.50),   # 命中 1
        (9.60, 9.70, 9.50, 9.60),       # 低点 9.50 < 5% 跌停价 9.98 -> 越界
        (10.08, 10.08, 10.00, 10.08),   # 恰好 +5%，越界后只此一次
    ])
    build_daily_flags(warehouse, days[0], days[-1])
    row = flags_of(warehouse, "600000.SH", days[-1])
    assert row["st_inferred_raw"] is True
    assert row["st_inference_rejected_by_price_evidence"] is True
    assert row["st_inferred_final"] is False


def test_the_veto_uses_the_band_not_a_fixed_percentage(warehouse):
    """低价股的分位取整会合法产生 >5% 涨幅，不能按百分比否决。

    前收 0.33 -> ST 涨停 round(0.3465, 2) = 0.35，涨幅 +6.06%。
    这是 5% 档下的合法成交，不构成否决证据。
    """
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    _st_series(warehouse, "600000.SH", days, [
        (0.33, 0.33, 0.33, 0.33),
        (0.35, 0.35, 0.34, 0.35),        # +6.06%，但恰好等于 ST 涨停价
        (0.37, 0.37, 0.36, 0.37),        # 0.35 x 1.05 = 0.3675 -> 0.37
    ])
    build_daily_flags(warehouse, days[0], days[-1])
    row = flags_of(warehouse, "600000.SH", days[-1])
    assert row["st_inference_rejected_by_price_evidence"] is False
    assert row["st_inferred_final"] is True


def test_the_veto_only_looks_backwards(warehouse):
    """未来的越界不能影响今天的判定——PIT 契约。"""
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
    _st_series(warehouse, "600000.SH", days, [
        (10.00, 10.00, 10.00, 10.00),
        (10.50, 10.50, 10.50, 10.50),    # +5%
        (11.03, 11.03, 11.03, 11.03),    # +5%，此刻应判 ST
        (12.20, 12.50, 12.10, 12.40),    # 之后才越界
    ])
    build_daily_flags(warehouse, days[0], days[-1])
    assert flags_of(warehouse, "600000.SH", days[2])["st_inferred_final"] is True
    assert flags_of(warehouse, "600000.SH", days[3])["st_inferred_final"] is False


def test_a_normal_stock_is_never_marked_st(warehouse):
    days = [date(2024, 1, 2), date(2024, 1, 3)]
    _st_series(warehouse, "600000.SH", days, [
        (10.00, 10.00, 10.00, 10.00),
        (10.20, 10.30, 10.10, 10.25),
    ])
    build_daily_flags(warehouse, days[0], days[-1])
    row = flags_of(warehouse, "600000.SH", days[-1])
    assert row["st_inferred_raw"] is False
    assert row["st_inference_rejected_by_price_evidence"] is False
    assert row["st_inferred_final"] is False
