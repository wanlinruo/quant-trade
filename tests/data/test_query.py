from datetime import date

import pyarrow as pa
import polars as pl

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def _seed(warehouse: Warehouse) -> None:
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": ["600036.SH", "600036.SH", "000001.SZ"],
                "dt": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 2)],
                "open": [30.0, 30.5, 10.0],
                "high": [31.0, 31.5, 10.5],
                "low": [29.5, 30.0, 9.8],
                "close": [30.5, 31.0, 10.2],
                "volume": [1000, 1100, 2000],
                "amount": [30500.0, 34100.0, 20400.0],
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )


def test_daily_bars_filters_by_date_range(warehouse: Warehouse):
    _seed(warehouse)
    q = WarehouseQuery(warehouse)
    df = q.daily_bars(None, date(2024, 1, 3), date(2024, 1, 3))
    assert df.height == 1
    assert df["symbol"].to_list() == ["600036.SH"]


def test_daily_bars_filters_by_symbols(warehouse: Warehouse):
    _seed(warehouse)
    q = WarehouseQuery(warehouse)
    df = q.daily_bars(["000001.SZ"], date(2024, 1, 1), date(2024, 12, 31))
    assert df["symbol"].to_list() == ["000001.SZ"]


def test_daily_bars_is_sorted_by_symbol_then_dt(warehouse: Warehouse):
    _seed(warehouse)
    q = WarehouseQuery(warehouse)
    df = q.daily_bars(None, date(2024, 1, 1), date(2024, 12, 31))
    assert df["symbol"].to_list() == ["000001.SZ", "600036.SH", "600036.SH"]
    assert df["dt"].to_list() == [date(2024, 1, 2), date(2024, 1, 2), date(2024, 1, 3)]


def test_daily_bars_on_empty_warehouse_returns_empty_frame_with_columns(warehouse: Warehouse):
    q = WarehouseQuery(warehouse)
    df = q.daily_bars(None, date(2024, 1, 1), date(2024, 12, 31))
    assert df.height == 0
    assert df.columns == [f.name for f in schema.BARS_DAILY]


def test_instruments_on_empty_warehouse_returns_empty_frame(warehouse: Warehouse):
    q = WarehouseQuery(warehouse)
    df = q.instruments()
    assert df.height == 0
    assert df.columns == [f.name for f in schema.INSTRUMENTS]


def test_empty_symbol_list_returns_nothing(warehouse: Warehouse):
    _seed(warehouse)
    q = WarehouseQuery(warehouse)
    assert q.daily_bars([], date(2024, 1, 1), date(2024, 12, 31)).height == 0
