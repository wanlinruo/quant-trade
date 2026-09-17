from datetime import date

import pyarrow as pa
import polars as pl

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def _seed(warehouse: Warehouse, write_daily_bars) -> None:
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["600036.SH", "600036.SH", "000001.SZ"],
        "dt": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 2)],
        "open": [30.0, 30.5, 10.0],
        "high": [31.0, 31.5, 10.5],
        "low": [29.5, 30.0, 9.8],
        "close": [30.5, 31.0, 10.2],
        "volume": [1000.0, 1100.0, 2000.0],
        "amount": [30500.0, 34100.0, 20400.0],
    }))


def test_daily_bars_filters_by_date_range(warehouse: Warehouse, write_daily_bars):
    _seed(warehouse, write_daily_bars)
    q = WarehouseQuery(warehouse)
    df = q.daily_bars(None, date(2024, 1, 3), date(2024, 1, 3))
    assert df.height == 1
    assert df["symbol"].to_list() == ["600036.SH"]


def test_daily_bars_filters_by_symbols(warehouse: Warehouse, write_daily_bars):
    _seed(warehouse, write_daily_bars)
    q = WarehouseQuery(warehouse)
    df = q.daily_bars(["000001.SZ"], date(2024, 1, 1), date(2024, 12, 31))
    assert df["symbol"].to_list() == ["000001.SZ"]


def test_daily_bars_is_sorted_by_symbol_then_dt(warehouse: Warehouse, write_daily_bars):
    _seed(warehouse, write_daily_bars)
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


def test_empty_symbol_list_returns_nothing(warehouse: Warehouse, write_daily_bars):
    _seed(warehouse, write_daily_bars)
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


# --- D-11：退市整理期只认 namechange；候选池剔除只读退市整理期池 -----------

def _name_history(warehouse, rows, *, sessions):
    """rows: [(symbol, name, start, end, ann, reason)]；按 sessions 建好池子。"""
    from qsys.data.pipeline.delisting import build_delisting_pool

    warehouse.write("name_history", pa.table({
        "symbol": [r[0] for r in rows], "name": [r[1] for r in rows],
        "start_date": [r[2] for r in rows], "end_date": [r[3] for r in rows],
        "ann_date": [r[4] for r in rows], "change_reason": [r[5] for r in rows],
    }, schema=schema.NAME_HISTORY))
    build_delisting_pool(warehouse, sessions[0], sessions[-1], extra_sessions=sessions)


def test_delisting_symbols_cover_the_period_announced_in_time(warehouse):
    _name_history(warehouse, [
        ("600532.SH", "退市未来", date(2023, 5, 30), None, date(2023, 5, 29), "退市整理期"),
        ("600532.SH", "*ST未来", date(2022, 7, 1), date(2023, 5, 29), date(2022, 6, 30), "*ST"),
        ("002417.SZ", "深南退", date(2023, 6, 1), date(2023, 6, 20), date(2023, 5, 31), "退市整理期"),
    ], sessions=[date(2023, 5, 29), date(2023, 6, 16), date(2023, 6, 21)])
    q = WarehouseQuery(warehouse)
    assert q.delisting_symbols(date(2023, 6, 16), announced_by=date(2023, 6, 15)) == {
        "600532.SH", "002417.SZ"}
    # 区间外、以及只有 *ST 的日子都不算
    assert q.delisting_symbols(date(2023, 5, 29), announced_by=date(2023, 5, 26)) == set()
    assert q.delisting_symbols(date(2023, 6, 21), announced_by=date(2023, 6, 20)) == {"600532.SH"}


def test_delisting_announced_after_the_cutoff_is_not_visible_yet(warehouse):
    """T 日开盘前只能用到 T-1 及以前公告的事件——回测与实盘同一口径。"""
    _name_history(warehouse, [
        ("600781.SH", "退市辅仁", date(2023, 5, 31), None, date(2023, 5, 31), "退市整理期"),
    ], sessions=[date(2023, 5, 31), date(2023, 6, 1)])
    q = WarehouseQuery(warehouse)
    assert q.delisting_symbols(date(2023, 5, 31), announced_by=date(2023, 5, 30)) == set()
    assert q.delisting_symbols(date(2023, 6, 1), announced_by=date(2023, 5, 31)) == {"600781.SH"}


def test_delisting_without_announcement_date_is_treated_as_known(warehouse):
    """公告日缺失时无法证明开盘前不可知；候选池宁可漏、不可错，按已知处理。"""
    _name_history(warehouse, [
        ("000606.SZ", "顺利退", date(2023, 6, 1), None, None, "退市整理期"),
    ], sessions=[date(2023, 6, 15)])
    assert WarehouseQuery(warehouse).delisting_symbols(
        date(2023, 6, 15), announced_by=date(2023, 6, 14)) == {"000606.SZ"}


def test_delisting_symbols_refuse_days_the_pool_has_not_built(warehouse):
    """池子没建的日子不能返回空集——那会让整理期标的静默进入候选池。"""
    import pytest

    q = WarehouseQuery(warehouse)
    with pytest.raises(RuntimeError, match="未覆盖"):
        q.delisting_symbols(date(2023, 6, 15), announced_by=date(2023, 6, 14))
    _name_history(warehouse, [], sessions=[date(2023, 6, 14)])
    with pytest.raises(RuntimeError, match="未覆盖"):
        q.delisting_symbols(date(2023, 6, 15), announced_by=date(2023, 6, 14))
    _name_history(warehouse, [], sessions=[date(2023, 6, 14), date(2023, 6, 15)])
    assert q.delisting_symbols(date(2023, 6, 15), announced_by=date(2023, 6, 14)) == set()
