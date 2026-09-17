"""D-20：分钟线按交易日分区。

全市场一天约 134 万根、一年约 3.2 亿根；按年单文件每天整年读回重写，节点内存扛不住。
"""

import os
from datetime import date, datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qsys.data import schema
from qsys.data.pipeline.minutes import import_minute_bars, split_minute_year
from qsys.data.query import WarehouseQuery


def _bars(day, symbol="600036.SH", n=3, close=10.2):
    start = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=31)
    times = [start + timedelta(minutes=i) for i in range(n)]
    return pa.table({
        "symbol": [symbol] * n, "dt": times, "open": [10.0] * n, "high": [10.5] * n,
        "low": [9.5] * n, "close": [close] * n, "volume": [100] * n, "amount": [1020.0] * n,
    }, schema=schema.MINUTE_BARS)


def _incoming(tmp_path, name, table):
    d = tmp_path / "in"
    d.mkdir(exist_ok=True)
    pq.write_table(table, d / name)


def _import(warehouse, tmp_path):
    """导入后归档，避免下次导入把旧文件再读一遍。"""
    return import_minute_bars(warehouse, tmp_path / "in", archive_dir=tmp_path / "done")


D1, D2 = date(2026, 9, 14), date(2026, 9, 15)


def test_day_partition_layout(warehouse):
    path = warehouse.write_day("minute_bars", _bars(D1), day=D1)
    assert path == warehouse.root / "minute_bars" / "year=2026" / "date=20260914" / "part.parquet"


def test_write_day_refuses_rows_from_another_day(warehouse):
    with pytest.raises(ValueError, match="2026-09-15"):
        warehouse.write_day("minute_bars", _bars(D2), day=D1)


def test_import_writes_one_file_per_day_and_leaves_other_days_untouched(warehouse, tmp_path):
    _incoming(tmp_path, "a.parquet", _bars(D1))
    _import(warehouse, tmp_path)
    first = warehouse.root / "minute_bars" / "year=2026" / "date=20260914" / "part.parquet"
    stamp = os.stat(first).st_mtime_ns

    _incoming(tmp_path, "b.parquet", _bars(D2, symbol="000001.SZ"))
    _import(warehouse, tmp_path)
    assert os.stat(first).st_mtime_ns == stamp
    assert (warehouse.root / "minute_bars" / "year=2026" / "date=20260915" / "part.parquet").exists()
    assert not (warehouse.root / "minute_bars" / "year=2026" / "part.parquet").exists()


def test_reimport_of_a_day_merges_and_later_values_win(warehouse, tmp_path):
    _incoming(tmp_path, "a.parquet", _bars(D1, close=10.2))
    _import(warehouse, tmp_path)
    _incoming(tmp_path, "b.parquet", _bars(D1, close=10.3, n=2))
    _import(warehouse, tmp_path)
    got = WarehouseQuery(warehouse).minute_bars(None, datetime(2026, 9, 14), datetime(2026, 9, 14, 23))
    assert got.height == 3
    assert got["close"].to_list() == [10.3, 10.3, 10.2]


def test_query_reads_legacy_year_files_for_years_without_day_partitions(warehouse, tmp_path):
    warehouse.write("minute_bars", _bars(date(2025, 12, 31)), year=2025)
    _incoming(tmp_path, "a.parquet", _bars(D1))
    _import(warehouse, tmp_path)
    got = WarehouseQuery(warehouse).minute_bars(None, datetime(2025, 12, 1), datetime(2026, 9, 30))
    assert got["dt"].dt.date().unique().sort().to_list() == [date(2025, 12, 31), D1]
    assert WarehouseQuery(warehouse).dataset_bounds("minute_bars")[1].date() == D1


def test_day_partitions_win_over_a_legacy_file_of_the_same_year(warehouse, tmp_path):
    """迁移半途两者并存时不能读出重复行。"""
    warehouse.write("minute_bars", _bars(D1, close=9.9), year=2026)
    warehouse.write_day("minute_bars", _bars(D1, close=10.2), day=D1)
    got = WarehouseQuery(warehouse).minute_bars(None, datetime(2026, 9, 14), datetime(2026, 9, 14, 23))
    assert got.height == 3 and set(got["close"]) == {10.2}


def test_split_year_moves_the_legacy_file_to_backup_after_verifying(warehouse, tmp_path):
    table = pa.concat_tables([_bars(D1), _bars(D2, symbol="000001.SZ", n=5)])
    warehouse.write("minute_bars", table, year=2026)
    before = WarehouseQuery(warehouse).minute_bars(None, datetime(2026, 1, 1), datetime(2026, 12, 31, 23))
    report = split_minute_year(warehouse, 2026, backup_dir=tmp_path / "backup")
    assert report == {"year": 2026, "days": 2, "rows": 8}
    assert not (warehouse.root / "minute_bars" / "year=2026" / "part.parquet").exists()
    assert (tmp_path / "backup" / "minute_bars_year=2026.parquet").exists()
    after = WarehouseQuery(warehouse).minute_bars(None, datetime(2026, 1, 1), datetime(2026, 12, 31, 23))
    assert after.sort(["symbol", "dt"]).equals(before.sort(["symbol", "dt"]))
