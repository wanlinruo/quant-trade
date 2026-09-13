from datetime import date

import polars as pl
import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.pipeline.adjust import (
    build_adj_factors,
    build_corporate_actions,
    daily_factor,
    paid_factors,
    verify_computed_factors,
)
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


def _seed(warehouse: Warehouse, write_daily_bars) -> None:
    """600036 在 2024-01-04 除权：10 送 10。除权前收 20，除权后 10。"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["600036.SH"] * 4,
        "dt": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)],
        "open": [19.0, 19.5, 10.0, 10.1],
        "high": [20.0, 20.5, 10.5, 10.6],
        "low": [18.5, 19.0, 9.8, 9.9],
        "close": [19.5, 20.0, 10.0, 10.2],
        "factor": [1.0, 1.0, 2.0, 2.0],
        "volume": [1000.0, 1000.0, 2000.0, 2000.0],
        "amount": [19500.0, 20000.0, 20000.0, 20400.0],
    }))
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


def test_build_writes_one_row_per_bar(warehouse: Warehouse, write_daily_bars):
    _seed(warehouse, write_daily_bars)
    assert build_adj_factors(warehouse) == 4
    assert WarehouseQuery(warehouse).adj_factors().height == 4


def test_factor_is_one_before_ex_date(warehouse: Warehouse, write_daily_bars):
    _seed(warehouse, write_daily_bars)
    build_adj_factors(warehouse)
    df = WarehouseQuery(warehouse).adj_factors().sort("dt")
    assert df["cum_factor"].to_list()[:2] == [pytest.approx(1.0), pytest.approx(1.0)]


def test_factor_doubles_from_ex_date(warehouse: Warehouse, write_daily_bars):
    _seed(warehouse, write_daily_bars)
    build_adj_factors(warehouse)
    df = WarehouseQuery(warehouse).adj_factors().sort("dt")
    assert df["cum_factor"].to_list()[2:] == [pytest.approx(2.0), pytest.approx(2.0)]


def test_direct_factor_wins_when_corporate_action_computation_disagrees(
    warehouse: Warehouse, write_daily_bars
):
    """价格因子取真值；corporate_actions 只再负责成交量股本因子。"""
    _seed(warehouse, write_daily_bars)
    path = warehouse.root / "paid_daily" / "year=2024" / "part.parquet"
    frame = pl.read_parquet(path).with_columns(
        pl.when(pl.col("dt") >= date(2024, 1, 4))
        .then(3.0)
        .otherwise(1.0)
        .alias("factor")
    )
    frame.write_parquet(path)

    build_adj_factors(warehouse)
    got = WarehouseQuery(warehouse).adj_factors().sort("dt")
    assert got["cum_factor"].to_list() == [1.0, 1.0, 3.0, 3.0]
    assert got["cum_share_factor"].to_list() == [1.0, 1.0, 2.0, 2.0]


def test_adjusted_close_is_continuous_across_ex_date(warehouse: Warehouse, write_daily_bars):
    """除权前后后复权收盘价不应跳变：20.0 -> 10.0 x 2 = 20.0。"""
    _seed(warehouse, write_daily_bars)
    build_adj_factors(warehouse)
    q = WarehouseQuery(warehouse)
    bars = q.daily_bars(None, date(2024, 1, 1), date(2024, 12, 31))
    factors = q.adj_factors()
    joined = bars.join(factors, on=["symbol", "dt"]).sort("dt")
    adjusted = (joined["close"] * joined["cum_factor"]).to_list()
    assert adjusted[1] == pytest.approx(20.0)
    assert adjusted[2] == pytest.approx(20.0)


def test_build_is_idempotent(warehouse: Warehouse, write_daily_bars):
    _seed(warehouse, write_daily_bars)
    build_adj_factors(warehouse)
    build_adj_factors(warehouse)
    assert WarehouseQuery(warehouse).adj_factors().height == 4


def test_build_on_empty_warehouse_returns_zero(warehouse: Warehouse):
    assert build_adj_factors(warehouse) == 0


# --- build_corporate_actions：计划遗漏的一环，缺了它复权因子会静默全为 1.0 ---


class _FakeReference:
    name = "fake-ref"

    def __init__(self, actions: dict[str, pl.DataFrame], failing: set[str] | None = None):
        self._actions = actions
        self._failing = failing or set()

    def fetch_instruments(self):
        raise NotImplementedError

    def fetch_sessions(self, start, end):
        raise NotImplementedError

    def fetch_corporate_actions(self, symbol):
        if symbol in self._failing:
            raise RuntimeError("boom")
        return self._actions.get(symbol, pl.DataFrame(schema=_ACTION_SCHEMA))


_ACTION_SCHEMA = {
    "ex_date": pl.Date,
    "cash_per_share": pl.Float64,
    "stock_per_share": pl.Float64,
    "rights_per_share": pl.Float64,
    "rights_price": pl.Float64,
}


def _actions(ex: date, cash: float, stock: float) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ex_date": [ex],
            "cash_per_share": [cash],
            "stock_per_share": [stock],
            "rights_per_share": [0.0],
            "rights_price": [0.0],
        },
        schema=_ACTION_SCHEMA,
    )


def test_build_corporate_actions_writes_rows(warehouse: Warehouse):
    provider = _FakeReference({"600036.SH": _actions(date(2024, 1, 4), 0.0, 1.0)})
    assert build_corporate_actions(provider, warehouse, ["600036.SH"]) == 1
    df = WarehouseQuery(warehouse).corporate_actions()
    assert df["symbol"].to_list() == ["600036.SH"]
    assert df["stock_per_share"].to_list() == [1.0]


def test_build_corporate_actions_is_idempotent(warehouse: Warehouse):
    provider = _FakeReference({"600036.SH": _actions(date(2024, 1, 4), 0.0, 1.0)})
    build_corporate_actions(provider, warehouse, ["600036.SH"])
    build_corporate_actions(provider, warehouse, ["600036.SH"])
    assert WarehouseQuery(warehouse).corporate_actions().height == 1


def test_build_corporate_actions_survives_single_symbol_failure(warehouse: Warehouse):
    provider = _FakeReference(
        {"000001.SZ": _actions(date(2024, 1, 4), 0.5, 0.0)}, failing={"600036.SH"}
    )
    written = build_corporate_actions(provider, warehouse, ["600036.SH", "000001.SZ"])
    assert written == 1
    assert WarehouseQuery(warehouse).corporate_actions()["symbol"].to_list() == ["000001.SZ"]


def test_build_corporate_actions_with_no_events_writes_nothing(warehouse: Warehouse):
    assert build_corporate_actions(_FakeReference({}), warehouse, ["600036.SH"]) == 0


def test_direct_price_factors_survive_when_corporate_actions_are_missing(
    warehouse: Warehouse, write_daily_bars
):
    """缺 corporate_actions 只降级成交量因子，不能抹掉直取价格因子。"""
    _seed_bars_only(warehouse, write_daily_bars)
    build_adj_factors(warehouse)
    factors = WarehouseQuery(warehouse).adj_factors()
    assert factors["cum_factor"].to_list() == [1.0, 1.5]
    assert set(factors["cum_share_factor"].to_list()) == {1.0}


def _seed_bars_only(warehouse: Warehouse, write_daily_bars) -> None:
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["600036.SH"] * 2,
        "dt": [date(2024, 1, 2), date(2024, 1, 3)],
        "open": [19.0, 19.5],
        "high": [20.0, 20.5],
        "low": [18.5, 19.0],
        "close": [19.5, 20.0],
        "factor": [1.0, 1.5],
        "volume": [1000.0, 1000.0],
        "amount": [19500.0, 20000.0],
    }))


def test_factor_is_read_directly_from_the_paid_source(warehouse, write_daily_bars):
    day = date(2026, 9, 9)
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [day], "name": ["平安银行"],
        "open": [11.76], "high": [11.79], "low": [11.69], "close": [11.70],
        "pre_close": [11.70], "volume": [100.0], "amount": [1e5],
        "factor": [139.008],
    }))
    got = paid_factors(WarehouseQuery(warehouse), None, day, day)
    assert got.to_dicts() == [{"symbol": "000001.SZ", "dt": day, "factor": 139.008}]


def test_verification_reports_the_gap_without_overwriting(warehouse, write_daily_bars):
    days = [date(2026, 6, 11), date(2026, 6, 12)]
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ", "000001.SZ"], "dt": days,
        "name": ["平安银行", "平安银行"],
        "open": [11.30, 10.94], "high": [11.30, 10.94],
        "low": [11.30, 10.94], "close": [11.30, 10.94],
        "pre_close": [11.30, 10.94], "volume": [100.0, 100.0],
        "amount": [1e5, 1e5], "factor": [134.5794, 139.008],
    }))
    report = verify_computed_factors(WarehouseQuery(warehouse), days[0], days[1])
    assert report.height == 1
    assert report.row(0, named=True)["dt"] == days[1]


def test_verification_carries_state_across_years(warehouse, write_daily_bars):
    days = [date(2025, 12, 31), date(2026, 1, 2)]
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ", "000001.SZ"], "dt": days,
        "name": ["平安银行", "平安银行"],
        "open": [10.0, 5.0], "high": [10.0, 5.0], "low": [10.0, 5.0],
        "close": [10.0, 5.0], "pre_close": [10.0, 5.0],
        "volume": [100.0, 100.0], "amount": [1e5, 1e5],
        # 价格反推是 2 倍，直取故意写成 3 倍；跨年首行必须报出来。
        "factor": [1.0, 3.0],
    }))
    report = verify_computed_factors(WarehouseQuery(warehouse), days[0], days[1])
    assert report.height == 1
    assert report.row(0, named=True)["dt"] == days[1]
