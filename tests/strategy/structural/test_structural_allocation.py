"""S8 权重分配，对照原文 L976-L1010。"""

import pytest

from qsys.strategy.structural.allocation import allocate, fee_reserve


def test_a_single_candidate_gets_the_whole_budget():
    """只有一只时它拿全部可用资金——权重在当日候选内部归一化。"""
    budgets = allocate([("600001.SH", "LOW_MAIN")], cash=100_000.0, opens={"600001.SH": 10.0})
    assert len(budgets) == 1
    assert budgets[0].budget == pytest.approx(100_000.0)


def test_weights_are_normalised_within_the_day():
    """LOW_MAIN 1.15 : LOW_CHINEXT 1.00 -> 分成 1.15/2.15 与 1.00/2.15。"""
    budgets = allocate(
        [("600001.SH", "LOW_MAIN"), ("300001.SZ", "LOW_CHINEXT")],
        cash=215_000.0,
        opens={"600001.SH": 10.0, "300001.SZ": 10.0},
    )
    by_symbol = {b.symbol: b.budget for b in budgets}
    assert by_symbol["600001.SH"] == pytest.approx(115_000.0)
    assert by_symbol["300001.SZ"] == pytest.approx(100_000.0)


def test_core_branches_weigh_more_than_tactical_ones():
    budgets = allocate(
        [("600001.SH", "HIGH_MAIN"), ("300001.SZ", "HIGH_CHINEXT")],
        cash=200_000.0,
        opens={"600001.SH": 10.0, "300001.SZ": 10.0},
    )
    by_symbol = {b.symbol: b.budget for b in budgets}
    assert by_symbol["600001.SH"] > by_symbol["300001.SZ"]


def test_an_unknown_tag_weighs_one():
    budgets = allocate(
        [("600001.SH", "WHAT_EVER")], cash=50_000.0, opens={"600001.SH": 10.0}
    )
    assert budgets[0].budget == pytest.approx(50_000.0)


def test_a_budget_below_one_lot_is_skipped():
    """budget / open < 100 股就下不了单（原文 L1027）。"""
    budgets = allocate(
        [("600001.SH", "LOW_MAIN")], cash=500.0, opens={"600001.SH": 10.0}
    )
    assert budgets[0].qty == 0
    assert budgets[0].status == "BELOW_BOARD_LOT"


def test_exactly_one_lot_is_allowed():
    """1,010 元买 10.00 的股：保护价 10.02、准备金 5.01，本金 1,004.99 -> 100 股。"""
    budgets = allocate(
        [("600001.SH", "LOW_MAIN")], cash=1010.0, opens={"600001.SH": 10.0}
    )
    assert budgets[0].qty == 100
    assert budgets[0].status == "ORDER_SUBMITTED"


def test_the_price_buffer_and_fee_reserve_shrink_the_size():
    """旧口径 1,000 / 10.00 恰好一手；加上 0.2% 保护价与费用准备金就买不起了。

    这不是保守过头：不预留就会出现"按未滑点价用满现金、实际按滑点价成交"
    的透支。聚宽未加保护时 241 天里 44 天现金为负。
    """
    budgets = allocate(
        [("600001.SH", "LOW_MAIN")], cash=1000.0, opens={"600001.SH": 10.0}
    )
    assert budgets[0].qty == 0
    assert budgets[0].status == "BELOW_BOARD_LOT"


def test_the_protect_price_is_the_open_lifted_by_the_buffer():
    budgets = allocate(
        [("600001.SH", "LOW_MAIN")], cash=100_000.0, opens={"600001.SH": 10.0}
    )
    assert budgets[0].protect_price == pytest.approx(10.02)


def test_the_protect_price_never_exceeds_the_limit_up():
    """开盘就在涨停价附近时，保护价不能挂到涨停之上——那种单挂不进去。"""
    budgets = allocate(
        [("600001.SH", "LOW_MAIN")], cash=100_000.0,
        opens={"600001.SH": 11.00}, limit_ups={"600001.SH": 11.00},
    )
    assert budgets[0].protect_price == pytest.approx(11.00)


def test_a_missing_limit_up_falls_back_to_the_plain_buffer():
    budgets = allocate(
        [("600001.SH", "LOW_MAIN")], cash=100_000.0,
        opens={"600001.SH": 10.0}, limit_ups={},
    )
    assert budgets[0].protect_price == pytest.approx(10.02)


def test_the_fee_reserve_matches_the_original_formula():
    """原文：max(5, 预算 x 0.0003) + 预算 x 0.00001，与实际成本模型无关。"""
    assert fee_reserve(50_000.0) == pytest.approx(15.5)   # max(5, 15) + 0.5
    assert fee_reserve(1_000.0) == pytest.approx(5.01)    # max(5, 0.3) + 0.01
    assert fee_reserve(0.0) == 0.0


def test_quantity_is_rounded_down_to_whole_lots():
    budgets = allocate(
        [("600001.SH", "LOW_MAIN")], cash=10_550.0, opens={"600001.SH": 10.0}
    )
    assert budgets[0].qty == 1000


def test_no_cash_means_nothing_is_ordered():
    budgets = allocate(
        [("600001.SH", "LOW_MAIN")], cash=0.0, opens={"600001.SH": 10.0}
    )
    assert budgets[0].qty == 0
    assert budgets[0].status == "NO_AVAILABLE_CASH"


def test_a_missing_open_price_is_skipped():
    budgets = allocate([("600001.SH", "LOW_MAIN")], cash=100_000.0, opens={})
    assert budgets[0].qty == 0
    assert budgets[0].status == "INVALID_OPEN_PRICE"


def test_a_non_positive_open_price_is_skipped():
    budgets = allocate(
        [("600001.SH", "LOW_MAIN")], cash=100_000.0, opens={"600001.SH": 0.0}
    )
    assert budgets[0].status == "INVALID_OPEN_PRICE"


def test_no_candidates_yields_nothing():
    assert allocate([], cash=100_000.0, opens={}) == []


def test_live_risk_budget_caps_each_candidate_without_redistributing():
    rows = allocate(
        [("600036.SH", "HIGH_MAIN")], 10_000.0,
        {"600036.SH": 10.0}, max_budget=5_000.0,
    )
    assert rows[0].budget == 5_000.0
    assert rows[0].qty == 400


def test_every_candidate_gets_a_row_even_when_skipped():
    """跳过的也要留痕，否则说不清是没选中还是买不起。"""
    budgets = allocate(
        [("600001.SH", "LOW_MAIN"), ("300001.SZ", "LOW_CHINEXT")],
        cash=1000.0,
        opens={"600001.SH": 10.0},
    )
    assert len(budgets) == 2
    assert {b.symbol for b in budgets} == {"600001.SH", "300001.SZ"}
