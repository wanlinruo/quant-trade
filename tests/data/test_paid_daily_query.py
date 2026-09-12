"""查询层换底：daily_bars 读付费源，旧表只经 legacy_daily_bars 读。"""

from datetime import date

import polars as pl
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.query import WarehouseQuery

DAYS = [date(2026, 9, 9), date(2026, 9, 10)]


def _write_paid(warehouse, rows):
    """直接落 paid_daily 分区——该表永久只读，不经 Warehouse.write。"""
    frame = pl.DataFrame(rows)
    table = frame.to_arrow().cast(schema.PAID_DAILY)
    path = warehouse.root / "paid_daily" / "year=2026" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def _paid_row(symbol, dt, close, volume):
    row = {"dt": dt, "symbol": symbol, "name": "平安银行"}
    for field in schema.PAID_DAILY.names:
        if field in row:
            continue
        row[field] = 0.0
    row |= {"open": close, "high": close, "low": close, "close": close,
            "pre_close": close, "volume": volume, "amount": close * volume * 100,
            "factor": 1.0}
    return row


def test_daily_bars_reads_the_paid_source(warehouse):
    _write_paid(warehouse, [_paid_row("000001.SZ", DAYS[0], 11.70, 582305.98)])
    got = WarehouseQuery(warehouse).daily_bars(None, DAYS[0], DAYS[-1])
    assert got.height == 1
    assert got["close"].to_list() == [11.70]


def test_daily_bars_keeps_the_fractional_lot_count(warehouse):
    """付费源 volume 带小数，取整是有损变换。"""
    _write_paid(warehouse, [_paid_row("000001.SZ", DAYS[0], 11.70, 582305.98)])
    got = WarehouseQuery(warehouse).daily_bars(None, DAYS[0], DAYS[-1])
    assert got["volume"].dtype == pl.Float64
    assert got["volume"].to_list() == [582305.98]


def test_daily_bars_projects_exactly_the_eight_columns(warehouse):
    """32 处调用点依赖这 8 列，多给或少给都会波及下游。"""
    _write_paid(warehouse, [_paid_row("000001.SZ", DAYS[0], 11.70, 100.0)])
    got = WarehouseQuery(warehouse).daily_bars(None, DAYS[0], DAYS[-1])
    assert got.columns == list(schema.DAILY_BARS.names)


def test_daily_bars_is_empty_with_the_right_schema_when_nothing_is_stored(warehouse):
    got = WarehouseQuery(warehouse).daily_bars(None, DAYS[0], DAYS[-1])
    assert got.height == 0
    assert got.columns == list(schema.DAILY_BARS.names)
    assert got["volume"].dtype == pl.Float64


def test_paid_daily_exposes_all_twenty_eight_columns(warehouse):
    _write_paid(warehouse, [_paid_row("000001.SZ", DAYS[0], 11.70, 100.0)])
    got = WarehouseQuery(warehouse).paid_daily(None, DAYS[0], DAYS[-1])
    assert got.columns == list(schema.PAID_DAILY.names)
    assert got["name"].to_list() == ["平安银行"]


def test_legacy_daily_bars_still_reads_the_frozen_table(warehouse):
    """旧表冻结但不删——这次回归要拿它逐键对账。"""
    old = pl.DataFrame(
        {"symbol": ["000001.SZ"], "dt": [DAYS[0]], "open": [11.76], "high": [11.79],
         "low": [11.69], "close": [11.70], "volume": [582306], "amount": [6.8e8]}
    )
    warehouse.write("bars_daily", old.to_arrow().cast(schema.BARS_DAILY), year=2026)
    got = WarehouseQuery(warehouse).legacy_daily_bars(None, DAYS[0], DAYS[-1])
    assert got["volume"].to_list() == [582306]
    assert got["volume"].dtype == pl.Int64


def test_daily_bars_does_not_fall_back_to_the_frozen_table(warehouse):
    """只写了旧表时 daily_bars 必须返回空。

    悄悄回退到旧表，就等于在付费源缺数据的日子里用另一个源顶上，
    而调用方不会知道——这正是要消除的双主表歧义。
    """
    old = pl.DataFrame(
        {"symbol": ["000001.SZ"], "dt": [DAYS[0]], "open": [11.76], "high": [11.79],
         "low": [11.69], "close": [11.70], "volume": [582306], "amount": [6.8e8]}
    )
    warehouse.write("bars_daily", old.to_arrow().cast(schema.BARS_DAILY), year=2026)
    assert WarehouseQuery(warehouse).daily_bars(None, DAYS[0], DAYS[-1]).height == 0
