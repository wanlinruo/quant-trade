"""S8 权重分配，对照原文 L976-L1010。"""

import pytest

from qsys.strategy.structural.allocation import allocate


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
    budgets = allocate(
        [("600001.SH", "LOW_MAIN")], cash=1000.0, opens={"600001.SH": 10.0}
    )
    assert budgets[0].qty == 100
    assert budgets[0].status == "ORDER_SUBMITTED"


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


def test_every_candidate_gets_a_row_even_when_skipped():
    """跳过的也要留痕，否则说不清是没选中还是买不起。"""
    budgets = allocate(
        [("600001.SH", "LOW_MAIN"), ("300001.SZ", "LOW_CHINEXT")],
        cash=1000.0,
        opens={"600001.SH": 10.0},
    )
    assert len(budgets) == 2
    assert {b.symbol for b in budgets} == {"600001.SH", "300001.SZ"}
