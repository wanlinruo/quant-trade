from datetime import datetime

import pytest

from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol
from qsys.live.messages import AccountSnapshot, PositionSnapshot
from qsys.live.reconcile import compare_sim_vs_live, reconcile_fills, reconcile_positions

CMB = Symbol("600036", Market.SH)
PAB = Symbol("000001", Market.SZ)
T = datetime(2024, 1, 3, 10, 0)


def local(cash: float, holdings: dict[Symbol, int]) -> Portfolio:
    p = Portfolio(cash)
    for symbol, qty in holdings.items():
        p.apply_fill(Fill("o", symbol, Side.BUY, qty, 10.0, 0.0, T))
    p.unfreeze()
    p.cash = cash  # 直接设回目标值以便精确控制测试
    return p


def remote(cash: float, holdings: dict[str, int]) -> AccountSnapshot:
    return AccountSnapshot(
        at=T, cash=cash,
        positions={s: PositionSnapshot(total_qty=q, available_qty=q, avg_cost=10.0)
                   for s, q in holdings.items()},
    )


def test_matching_positions_are_consistent():
    r = reconcile_positions(local(100_000.0, {CMB: 500}), remote(100_000.0, {"600036.SH": 500}))
    assert r.is_consistent is True
    assert r.mismatches == ()


def test_quantity_mismatch_is_flagged():
    r = reconcile_positions(local(100_000.0, {CMB: 500}), remote(100_000.0, {"600036.SH": 400}))
    assert r.is_consistent is False
    assert r.mismatches[0].local_qty == 500
    assert r.mismatches[0].remote_qty == 400


def test_position_missing_remotely_is_flagged():
    r = reconcile_positions(local(100_000.0, {CMB: 500}), remote(100_000.0, {}))
    assert r.is_consistent is False
    assert r.mismatches[0].remote_qty == 0


def test_position_missing_locally_is_flagged():
    r = reconcile_positions(local(100_000.0, {}), remote(100_000.0, {"600036.SH": 500}))
    assert r.is_consistent is False
    assert r.mismatches[0].local_qty == 0


def test_cash_within_tolerance_is_consistent():
    r = reconcile_positions(local(100_000.0, {}), remote(100_000.50, {}), cash_tolerance=1.0)
    assert r.is_consistent is True


def test_cash_beyond_tolerance_is_flagged():
    r = reconcile_positions(local(100_000.0, {}), remote(99_000.0, {}), cash_tolerance=1.0)
    assert r.is_consistent is False
    assert r.cash_diff == pytest.approx(1000.0)


def test_summary_lists_every_mismatch():
    r = reconcile_positions(
        local(100_000.0, {CMB: 500, PAB: 300}), remote(99_000.0, {"600036.SH": 400})
    )
    text = r.summary()
    assert "600036.SH" in text and "000001.SZ" in text
    assert "1000" in text or "1,000" in text


def test_empty_both_sides_is_consistent():
    r = reconcile_positions(local(100_000.0, {}), remote(100_000.0, {}))
    assert r.is_consistent is True


def fill(oid: str, qty: int = 100, price: float = 10.0) -> Fill:
    return Fill(oid, CMB, Side.BUY, qty, price, 0.0, T)


def test_matching_fills_are_consistent():
    r = reconcile_fills([fill("a"), fill("b")], [fill("a"), fill("b")])
    assert r.is_consistent is True


def test_fill_missing_remotely_is_flagged():
    r = reconcile_fills([fill("a"), fill("b")], [fill("a")])
    assert r.is_consistent is False
    assert r.mismatches[0].kind == "missing_remote"
    assert r.mismatches[0].client_order_id == "b"


def test_fill_missing_locally_is_flagged():
    r = reconcile_fills([fill("a")], [fill("a"), fill("b")])
    assert r.mismatches[0].kind == "missing_local"


def test_quantity_difference_is_flagged():
    r = reconcile_fills([fill("a", qty=100)], [fill("a", qty=90)])
    assert r.mismatches[0].kind == "qty_differs"
    assert "100" in r.mismatches[0].detail and "90" in r.mismatches[0].detail


def test_price_difference_is_flagged():
    r = reconcile_fills([fill("a", price=10.0)], [fill("a", price=10.5)])
    assert r.mismatches[0].kind == "price_differs"


def test_identical_fills_have_zero_deviation():
    report = compare_sim_vs_live([fill("a", price=10.0)], [fill("a", price=10.0)])
    assert report.mean_deviation == 0.0
    assert report.max_deviation == 0.0


def test_deviation_is_measured_per_order():
    report = compare_sim_vs_live([fill("a", price=10.0)], [fill("a", price=10.1)])
    assert report.samples[0].deviation == pytest.approx(0.01)


def test_unmatched_orders_are_skipped():
    report = compare_sim_vs_live([fill("a"), fill("b")], [fill("a")])
    assert len(report.samples) == 1


def test_deviation_within_model_is_explained():
    """差异能被滑点模型解释，即引擎与实盘一致。"""
    report = compare_sim_vs_live([fill("a", price=10.0)], [fill("a", price=10.01)])
    assert report.explained_by(0.002) is True


def test_deviation_beyond_model_is_unexplained():
    report = compare_sim_vs_live([fill("a", price=10.0)], [fill("a", price=11.0)])
    assert report.explained_by(0.002) is False


def test_empty_comparison_is_explained():
    report = compare_sim_vs_live([], [])
    assert report.explained_by(0.002) is True


# ---- 国债逆回购是现金等价物，不是持仓（2026-09-28）----

def test_reverse_repo_is_not_treated_as_a_position():
    """维护者用闲置资金买 7 天逆回购，对账当场判失败、影子核心停了一整天。

    131xxx / 204xxx 是生息用的现金等价物，不由策略买卖，当成持仓比对永远对不上。
    """
    r = reconcile_positions(
        local(100_000.0, {}),
        remote(100_000.0, {"131801.SZ": 1000, "131990.SZ": 1000}),
    )

    assert r.is_consistent is True
    assert r.mismatches == ()


def test_a_real_position_is_still_flagged_alongside_repo():
    """把逆回购放行，不等于把真正的持仓差异也放过。"""
    r = reconcile_positions(
        local(100_000.0, {}),
        remote(100_000.0, {"131801.SZ": 1000, "600036.SH": 500}),
    )

    assert r.is_consistent is False
    assert [m.symbol for m in r.mismatches] == ["600036.SH"]


@pytest.mark.parametrize("symbol,expected", [
    ("131801.SZ", True), ("131990.SZ", True), ("204001.SH", True),
    ("600036.SH", False), ("000001.SZ", False), ("300750.SZ", False),
])
def test_which_codes_count_as_cash_equivalents(symbol, expected):
    from qsys.live.reconcile import is_cash_equivalent

    assert is_cash_equivalent(symbol) is expected
