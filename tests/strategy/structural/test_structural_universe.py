"""候选池：昨日首板与昨日炸板。"""

from datetime import date, timedelta

import polars as pl

from qsys.strategy.structural.universe import board_of, candidate_pool, strategy_tag

D = [date(2024, 1, d) for d in (2, 3, 4)]


def test_board_of_chinext():
    assert board_of("300750.SZ") == "CHINEXT"
    assert board_of("301001.SZ") == "CHINEXT"


def test_board_of_main():
    assert board_of("600036.SH") == "MAIN"
    assert board_of("000001.SZ") == "MAIN"


def test_strategy_tag_combines_name_and_board():
    assert strategy_tag("LOW", "600036.SH") == "LOW_MAIN"
    assert strategy_tag("HIGH", "300750.SZ") == "HIGH_CHINEXT"


def flags_frame(rows):
    """rows: [(symbol, dt, is_limit_up, touched, is_first_board)]"""
    n = len(rows)
    return pl.DataFrame(
        {
            "dt": [r[1] for r in rows],
            "symbol": [r[0] for r in rows],
            "prev_close": [10.0] * n,
            "limit_up": [11.0] * n,
            "limit_down": [9.0] * n,
            "is_limit_up": [r[2] for r in rows],
            "is_limit_down": [False] * n,
            "touched_limit_up": [r[3] for r in rows],
            "is_broken_board": [r[3] and not r[2] for r in rows],
            "is_first_board": [r[4] for r in rows],
        }
    )


def listed(*symbols, when=date(2020, 1, 1)):
    return {s: when for s in symbols}


def test_first_boards_come_from_the_previous_session():
    pool = candidate_pool(
        flags_frame([("600001.SH", D[1], True, True, True)]),
        listed_since=listed("600001.SH"),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == ["600001.SH"]


def test_broken_boards_are_collected_separately():
    pool = candidate_pool(
        flags_frame([("600002.SH", D[1], False, True, False)]),
        listed_since=listed("600002.SH"),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == []
    assert pool.broken_boards == ["600002.SH"]


def test_star_and_bse_are_excluded():
    """filter_kcbj_stock：排除 4/8 开头（北交所）与 68 开头（科创板）。"""
    symbols = ("688001.SH", "430001.BJ", "830001.BJ", "600001.SH")
    pool = candidate_pool(
        flags_frame([(s, D[1], True, True, True) for s in symbols]),
        listed_since=listed(*symbols),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == ["600001.SH"]


def test_chinext_is_kept():
    pool = candidate_pool(
        flags_frame([("300001.SZ", D[1], True, True, True)]),
        listed_since=listed("300001.SZ"),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == ["300001.SZ"]


def test_new_stocks_are_excluded():
    """上市不满 50 天剔除。上市日取自日线首个 bar。"""
    pool = candidate_pool(
        flags_frame([("600001.SH", D[1], True, True, True)]),
        listed_since=listed("600001.SH", when=date(2023, 12, 20)),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == []


def test_a_stock_listed_exactly_50_days_ago_is_excluded():
    """原文是 start_date < date - 50 天，恰好 50 天不算数。"""
    pool = candidate_pool(
        flags_frame([("600001.SH", D[1], True, True, True)]),
        listed_since=listed("600001.SH", when=D[2] - timedelta(days=50)),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == []


def test_a_stock_listed_51_days_ago_is_kept():
    pool = candidate_pool(
        flags_frame([("600001.SH", D[1], True, True, True)]),
        listed_since=listed("600001.SH", when=D[2] - timedelta(days=51)),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == ["600001.SH"]


def test_a_symbol_without_a_listing_date_is_excluded():
    """不知道上市日就不能放行——宁可漏，不可错。"""
    pool = candidate_pool(
        flags_frame([("600001.SH", D[1], True, True, True)]),
        listed_since={},
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == []


def test_other_sessions_are_ignored():
    pool = candidate_pool(
        flags_frame(
            [
                ("600001.SH", D[0], True, True, True),
                ("600002.SH", D[1], True, True, True),
            ]
        ),
        listed_since=listed("600001.SH", "600002.SH"),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == ["600002.SH"]


def test_results_are_sorted():
    pool = candidate_pool(
        flags_frame(
            [
                ("600009.SH", D[1], True, True, True),
                ("600001.SH", D[1], True, True, True),
            ]
        ),
        listed_since=listed("600009.SH", "600001.SH"),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == ["600001.SH", "600009.SH"]


def test_empty_flags_yield_nothing():
    pool = candidate_pool(
        flags_frame([]).clear(), listed_since={}, day=D[2], previous=D[1]
    )
    assert pool.first_boards == []
    assert pool.broken_boards == []


def flags_with_st(rows):
    """rows: [(symbol, dt, is_first_board, is_st, limit_pct)]"""
    n = len(rows)
    return pl.DataFrame(
        {
            "dt": [r[1] for r in rows],
            "symbol": [r[0] for r in rows],
            "prev_close": [10.0] * n,
            "limit_up": [11.0] * n,
            "limit_down": [9.0] * n,
            "is_limit_up": [r[2] for r in rows],
            "is_limit_down": [False] * n,
            "touched_limit_up": [r[2] for r in rows],
            "is_broken_board": [False] * n,
            "is_first_board": [r[2] for r in rows],
            "is_st": [r[3] for r in rows],
            "limit_pct": [r[4] for r in rows],
        }
    )


def test_st_stocks_are_excluded_from_the_pool():
    """对应原策略 filter_st_paused_stock。ST 读直取真值，不再反推。"""
    pool = candidate_pool(
        flags_with_st(
            [
                ("600001.SH", D[1], True, True, 0.05),
                ("600002.SH", D[1], True, False, 0.10),
            ]
        ),
        listed_since=listed("600001.SH", "600002.SH"),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == ["600002.SH"]


def test_a_gem_st_stock_is_excluded_even_though_its_tier_is_twenty_percent():
    """这条守的是 D-14 那颗地雷。

    创业板 ST 的真实档位是 0.20。旧实现按 `limit_pct > 0.05` 判「非 ST」，
    修对 limit_pct（D-13）之后它会把 ST 股静默放进候选池，而且不报错。
    """
    pool = candidate_pool(
        flags_with_st([("300029.SZ", D[1], True, True, 0.20)]),
        listed_since=listed("300029.SZ"),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == []


def test_unknown_st_status_is_excluded_from_the_pool():
    """拿不到 ST 真值就不放行——不知道不等于不是。

    与同文件 `keep()` 里「不知道上市日就不放行」同一条规矩。
    """
    pool = candidate_pool(
        flags_with_st([("600001.SH", D[1], True, None, None)]),
        listed_since=listed("600001.SH"),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == []


def test_flags_without_an_st_column_are_not_filtered():
    """老分区根本没有这一列时不该把所有票当成 ST 排掉。"""
    pool = candidate_pool(
        flags_frame([("600001.SH", D[1], True, True, True)]),
        listed_since=listed("600001.SH"),
        day=D[2],
        previous=D[1],
    )
    assert pool.first_boards == ["600001.SH"]
