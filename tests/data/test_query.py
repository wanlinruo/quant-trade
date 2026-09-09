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


def test_partitioned_reads_do_not_leak_the_hive_partition_column(warehouse: Warehouse):
    """分区目录名是 year=2024，duckdb 会据此自动多返回一列 year。

    多出来的列会让「读出来 → 合并 → 写回去」在 concat 或 cast 处炸掉，
    而空仓库路径又走不到这个分支，很容易漏。
    """
    import pyarrow as pa

    cases = [
        ("daily_flags", schema.DAILY_FLAGS, lambda q: q.daily_flags()),
        ("market_regime", schema.MARKET_REGIME, lambda q: q.market_regime()),
        ("valuation_snapshot", schema.VALUATION_SNAPSHOT, lambda q: q.valuation()),
    ]
    for dataset, arrow_schema, read in cases:
        warehouse.write(dataset, pa.table(
            {f.name: pa.array([], type=f.type) for f in arrow_schema},
            schema=arrow_schema,
        ), year=2024)
        df = read(WarehouseQuery(warehouse))
        assert df.columns == list(arrow_schema.names), dataset


def _seed_minutes(warehouse: Warehouse) -> None:
    import pyarrow as pa
    from datetime import datetime

    warehouse.write(
        "minute_bars",
        pa.table(
            {
                "symbol": ["600036.SH"] * 3 + ["000001.SZ"],
                "dt": [
                    datetime(2024, 1, 2, 9, 31),
                    datetime(2024, 1, 2, 9, 32),
                    datetime(2024, 1, 2, 15, 0),
                    datetime(2024, 1, 2, 9, 31),
                ],
                "open": [10.0] * 4,
                "high": [10.5] * 4,
                "low": [9.5] * 4,
                "close": [10.2] * 4,
                "volume": [100] * 4,
                "amount": [1020.0] * 4,
            },
            schema=schema.MINUTE_BARS,
        ),
        year=2024,
    )


def test_minute_bars_filter_by_symbol_and_time(warehouse: Warehouse):
    from datetime import datetime

    _seed_minutes(warehouse)
    q = WarehouseQuery(warehouse)
    df = q.minute_bars(
        ["600036.SH"], datetime(2024, 1, 2, 9, 0), datetime(2024, 1, 2, 10, 0)
    )
    assert df.height == 2
    assert df["symbol"].unique().to_list() == ["600036.SH"]


def test_minute_bars_all_symbols(warehouse: Warehouse):
    from datetime import datetime

    _seed_minutes(warehouse)
    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 2, 0, 0), datetime(2024, 1, 2, 23, 59)
    )
    assert df.height == 4


def test_minute_bars_empty_symbol_list_returns_nothing(warehouse: Warehouse):
    from datetime import datetime

    _seed_minutes(warehouse)
    df = WarehouseQuery(warehouse).minute_bars(
        [], datetime(2024, 1, 2, 0, 0), datetime(2024, 1, 2, 23, 59)
    )
    assert df.height == 0


def test_minute_bars_on_empty_warehouse(warehouse: Warehouse):
    from datetime import datetime

    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 2, 0, 0)
    )
    assert df.height == 0
    assert df.columns == list(schema.MINUTE_BARS.names)


def test_share_capital_round_trips(warehouse: Warehouse):
    import pyarrow as pa

    warehouse.write(
        "share_capital",
        pa.table(
            {
                "symbol": ["600036.SH", "600036.SH"],
                "dt": [date(2023, 3, 31), date(2023, 6, 30)],
                "ann_date": [date(2023, 3, 31), date(2023, 6, 30)],
                "total": [2.521985e10, 2.521985e10],
                "circulating": [2.062894e10, 2.062894e10],
            },
            schema=schema.SHARE_CAPITAL,
        ),
    )
    df = WarehouseQuery(warehouse).share_capital()
    assert df.height == 2
    assert df.columns == list(schema.SHARE_CAPITAL.names)


def test_share_capital_filters_by_symbol(warehouse: Warehouse):
    import pyarrow as pa

    warehouse.write(
        "share_capital",
        pa.table(
            {
                "symbol": ["600036.SH", "000001.SZ"],
                "dt": [date(2023, 3, 31)] * 2,
                "ann_date": [date(2023, 3, 31)] * 2,
                "total": [1.0, 2.0],
                "circulating": [1.0, 2.0],
            },
            schema=schema.SHARE_CAPITAL,
        ),
    )
    df = WarehouseQuery(warehouse).share_capital(["600036.SH"])
    assert df["symbol"].to_list() == ["600036.SH"]


def test_share_capital_on_empty_warehouse(warehouse: Warehouse):
    df = WarehouseQuery(warehouse).share_capital()
    assert df.height == 0
    assert df.columns == list(schema.SHARE_CAPITAL.names)


def test_a_partition_written_before_a_column_was_added_still_reads(warehouse: Warehouse):
    """schema 加列后，旧分区仍要能读出来，缺的列为 null。

    派生表可以整体重算，但重算本身要先读旧分区——读不了就成了死锁。
    """
    import pyarrow as pa

    # 模拟旧版本写下的分区：少了 index_ma20_slope_5d 这一列
    old_fields = [f for f in schema.MARKET_REGIME if f.name != "index_ma20_slope_5d"]
    old_schema = pa.schema(old_fields)
    warehouse.root.joinpath("market_regime", "year=2024").mkdir(parents=True)
    import pyarrow.parquet as pq

    pq.write_table(
        pa.table(
            {f.name: pa.array([None], type=f.type) for f in old_fields},
            schema=old_schema,
        ),
        warehouse.root / "market_regime" / "year=2024" / "part.parquet",
    )

    df = WarehouseQuery(warehouse).market_regime()
    assert df.columns == list(schema.MARKET_REGIME.names)
    assert df.height == 1
    assert df["index_ma20_slope_5d"].to_list() == [None]
