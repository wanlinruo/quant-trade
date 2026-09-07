from datetime import date

import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.pipeline.adjust import build_adj_factors, daily_factor
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def test_no_action_factor_is_one():
    assert daily_factor(10.0, 0.0, 0.0, 0.0, 0.0) == pytest.approx(1.0)


def test_ten_for_ten_bonus_doubles_factor():
    # 10 送 10：每股送 1 股，除权参考价 = 10 / 2 = 5，因子 = 10 / 5 = 2
    assert daily_factor(10.0, 0.0, 1.0, 0.0, 0.0) == pytest.approx(2.0)


def test_cash_dividend_factor():
    # 前收 10 元，每股派 0.5 元：参考价 9.5，因子 = 10 / 9.5
    assert daily_factor(10.0, 0.5, 0.0, 0.0, 0.0) == pytest.approx(10.0 / 9.5)


def test_rights_issue_factor():
    # 前收 10，每股配 0.5 股，配股价 6：参考价 = (10 + 3) / 1.5
    assert daily_factor(10.0, 0.0, 0.0, 0.5, 6.0) == pytest.approx(10.0 / (13.0 / 1.5))


def test_combined_cash_and_bonus():
    # 前收 10，派 0.5，送 1 股：参考价 = (10 - 0.5) / 2 = 4.75，因子 = 10 / 4.75
    assert daily_factor(10.0, 0.5, 1.0, 0.0, 0.0) == pytest.approx(10.0 / 4.75)


def test_zero_reference_price_raises():
    with pytest.raises(ValueError, match="non-positive reference price"):
        daily_factor(1.0, 1.0, 0.0, 0.0, 0.0)


def _seed(warehouse: Warehouse) -> None:
    """600036 在 2024-01-04 除权：10 送 10。除权前收 20，除权后 10。"""
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": ["600036.SH"] * 4,
                "dt": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)],
                "open": [19.0, 19.5, 10.0, 10.1],
                "high": [20.0, 20.5, 10.5, 10.6],
                "low": [18.5, 19.0, 9.8, 9.9],
                "close": [19.5, 20.0, 10.0, 10.2],
                "volume": [1000, 1000, 2000, 2000],
                "amount": [19500.0, 20000.0, 20000.0, 20400.0],
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )
    warehouse.write(
        "corporate_actions",
        pa.table(
            {
                "symbol": ["600036.SH"],
                "ex_date": [date(2024, 1, 4)],
                "cash_per_share": [0.0],
                "stock_per_share": [1.0],
                "rights_per_share": [0.0],
                "rights_price": [0.0],
            },
            schema=schema.CORPORATE_ACTIONS,
        ),
    )


def test_build_writes_one_row_per_bar(warehouse: Warehouse):
    _seed(warehouse)
    assert build_adj_factors(warehouse) == 4
    assert WarehouseQuery(warehouse).adj_factors().height == 4


def test_factor_is_one_before_ex_date(warehouse: Warehouse):
    _seed(warehouse)
    build_adj_factors(warehouse)
    df = WarehouseQuery(warehouse).adj_factors().sort("dt")
    assert df["cum_factor"].to_list()[:2] == [pytest.approx(1.0), pytest.approx(1.0)]


def test_factor_doubles_from_ex_date(warehouse: Warehouse):
    _seed(warehouse)
    build_adj_factors(warehouse)
    df = WarehouseQuery(warehouse).adj_factors().sort("dt")
    assert df["cum_factor"].to_list()[2:] == [pytest.approx(2.0), pytest.approx(2.0)]


def test_adjusted_close_is_continuous_across_ex_date(warehouse: Warehouse):
    """除权前后后复权收盘价不应跳变：20.0 -> 10.0 x 2 = 20.0。"""
    _seed(warehouse)
    build_adj_factors(warehouse)
    q = WarehouseQuery(warehouse)
    bars = q.daily_bars(None, date(2024, 1, 1), date(2024, 12, 31))
    factors = q.adj_factors()
    joined = bars.join(factors, on=["symbol", "dt"]).sort("dt")
    adjusted = (joined["close"] * joined["cum_factor"]).to_list()
    assert adjusted[1] == pytest.approx(20.0)
    assert adjusted[2] == pytest.approx(20.0)


def test_build_is_idempotent(warehouse: Warehouse):
    _seed(warehouse)
    build_adj_factors(warehouse)
    build_adj_factors(warehouse)
    assert WarehouseQuery(warehouse).adj_factors().height == 4


def test_build_on_empty_warehouse_returns_zero(warehouse: Warehouse):
    assert build_adj_factors(warehouse) == 0
