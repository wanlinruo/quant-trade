from datetime import date, datetime

import polars as pl
import pytest

from qsys.backtest.corporate import (
    CorporateAction,
    actions_from_frame,
    apply_corporate_actions,
)
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)
PAB = Symbol("000001", Market.SZ)
T = datetime(2024, 1, 2, 15, 0)


def holding(qty: int, price: float = 20.0, symbol: Symbol = CMB) -> Portfolio:
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", symbol, Side.BUY, qty, price, 0.0, T))
    p.unfreeze()
    return p


def test_cash_dividend_credits_cash():
    p = holding(1000)
    cash_before = p.cash
    cash, shares = apply_corporate_actions(
        p, [CorporateAction(CMB, date(2024, 6, 14), 0.719, 0.0)]
    )
    assert cash == 719.00
    assert shares == 0
    assert p.cash == cash_before + 719.00


def test_stock_dividend_increases_holding():
    p = holding(1000)
    cash, shares = apply_corporate_actions(
        p, [CorporateAction(CMB, date(2024, 6, 14), 0.0, 1.0)]
    )
    assert shares == 1000
    assert cash == 0.0
    assert p.position(CMB).total_qty == 2000


def test_bonus_shares_are_immediately_sellable():
    """送转股到账后可卖——与当日买入不同，它不受交收约束。"""
    p = holding(1000)
    apply_corporate_actions(p, [CorporateAction(CMB, date(2024, 6, 14), 0.0, 1.0)])
    assert p.position(CMB).available_qty == 2000


def test_fractional_bonus_shares_are_rounded_down():
    """A 股送转不足一股的部分折现，此处简化为舍去。"""
    p = holding(150)
    _, shares = apply_corporate_actions(
        p, [CorporateAction(CMB, date(2024, 6, 14), 0.0, 0.3)]
    )
    assert shares == 45
    assert p.position(CMB).total_qty == 195


def test_combined_cash_and_stock():
    p = holding(1000)
    cash, shares = apply_corporate_actions(
        p, [CorporateAction(CMB, date(2024, 6, 14), 0.5, 0.2)]
    )
    assert cash == 500.00
    assert shares == 200
    assert p.position(CMB).total_qty == 1200


def test_action_on_unheld_symbol_is_ignored():
    p = holding(1000)
    cash, shares = apply_corporate_actions(
        p, [CorporateAction(PAB, date(2024, 6, 14), 1.0, 1.0)]
    )
    assert (cash, shares) == (0.0, 0)


def test_multiple_actions_accumulate():
    p = holding(1000)
    cash, shares = apply_corporate_actions(
        p,
        [
            CorporateAction(CMB, date(2024, 6, 14), 0.5, 0.0),
            CorporateAction(CMB, date(2024, 6, 14), 0.3, 0.0),
        ],
    )
    assert cash == 800.00


def test_market_value_is_preserved_across_a_pure_split():
    """10 送 10 后持仓翻倍、价格减半，账户总值不应跳变。"""
    p = holding(1000, price=20.0)
    before = p.total_value({CMB: 20.0})
    apply_corporate_actions(p, [CorporateAction(CMB, date(2024, 6, 14), 0.0, 1.0)])
    after = p.total_value({CMB: 10.0})
    assert before == after


def test_actions_from_frame_groups_by_ex_date():
    df = pl.DataFrame(
        {
            "symbol": ["600036.SH", "000001.SZ", "600036.SH"],
            "ex_date": [date(2024, 6, 14), date(2024, 6, 14), date(2024, 7, 11)],
            "cash_per_share": [0.5, 0.3, 1.0],
            "stock_per_share": [0.0, 0.0, 0.2],
        }
    )
    grouped = actions_from_frame(df)
    assert set(grouped) == {date(2024, 6, 14), date(2024, 7, 11)}
    assert len(grouped[date(2024, 6, 14)]) == 2
    assert grouped[date(2024, 7, 11)][0].stock_per_share == 0.2


def test_actions_from_empty_frame():
    df = pl.DataFrame(
        schema={
            "symbol": pl.Utf8, "ex_date": pl.Date,
            "cash_per_share": pl.Float64, "stock_per_share": pl.Float64,
        }
    )
    assert actions_from_frame(df) == {}


def test_negative_ratios_rejected():
    with pytest.raises(ValueError, match="must be >= 0"):
        CorporateAction(CMB, date(2024, 6, 14), -1.0, 0.0)
