"""分钟线导入：传输无关、幂等、单个坏文件不中断整批。"""

from datetime import datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.pipeline.minutes import import_minute_bars
from qsys.data.query import WarehouseQuery

T = datetime(2024, 1, 2, 9, 31)


def write_incoming(directory, name, symbol="600036.SH", n=3, start=T):
    directory.mkdir(parents=True, exist_ok=True)
    times = [start + timedelta(minutes=i) for i in range(n)]
    table = pa.table(
        {
            "symbol": [symbol] * n,
            "dt": times,
            "open": [10.0] * n,
            "high": [10.5] * n,
            "low": [9.5] * n,
            "close": [10.2] * n,
            "volume": [100] * n,
            "amount": [1020.0] * n,
        },
        schema=schema.MINUTE_BARS,
    )
    pq.write_table(table, directory / name)
    return directory / name


def test_imports_a_single_file(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet")
    result = import_minute_bars(warehouse, tmp_path / "in")
    assert result.files == 1
    assert result.rows == 3
    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 1), datetime(2024, 1, 3)
    )
    assert df.height == 3


def test_imports_multiple_files(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet", symbol="600036.SH")
    write_incoming(tmp_path / "in", "b.parquet", symbol="000001.SZ")
    result = import_minute_bars(warehouse, tmp_path / "in")
    assert result.files == 2
    assert result.rows == 6


def test_reimporting_the_same_data_is_idempotent(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet")
    import_minute_bars(warehouse, tmp_path / "in")
    write_incoming(tmp_path / "in", "a.parquet")
    import_minute_bars(warehouse, tmp_path / "in")
    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 1), datetime(2024, 1, 3)
    )
    assert df.height == 3


def test_a_bad_file_does_not_abort_the_batch(warehouse, tmp_path):
    incoming = tmp_path / "in"
    write_incoming(incoming, "good.parquet")
    (incoming / "bad.parquet").write_bytes(b"not a parquet file")
    result = import_minute_bars(warehouse, incoming)
    assert result.rows == 3
    assert result.errors == 1


def test_wrong_schema_is_rejected_not_written(warehouse, tmp_path):
    incoming = tmp_path / "in"
    incoming.mkdir(parents=True)
    pq.write_table(pa.table({"symbol": ["x"], "oops": [1]}), incoming / "wrong.parquet")
    result = import_minute_bars(warehouse, incoming)
    assert result.errors == 1
    assert result.rows == 0


def test_archived_files_are_moved_out_of_incoming(warehouse, tmp_path):
    incoming, archive = tmp_path / "in", tmp_path / "done"
    write_incoming(incoming, "a.parquet")
    import_minute_bars(warehouse, incoming, archive_dir=archive)
    assert list(incoming.glob("*.parquet")) == []
    assert len(list(archive.glob("*.parquet"))) == 1


def test_bad_files_are_not_archived(warehouse, tmp_path):
    """坏文件留在原地，才有得查。"""
    incoming, archive = tmp_path / "in", tmp_path / "done"
    incoming.mkdir(parents=True)
    (incoming / "bad.parquet").write_bytes(b"garbage")
    import_minute_bars(warehouse, incoming, archive_dir=archive)
    assert (incoming / "bad.parquet").exists()


def test_files_stay_put_when_no_archive_given(warehouse, tmp_path):
    incoming = tmp_path / "in"
    write_incoming(incoming, "a.parquet")
    import_minute_bars(warehouse, incoming)
    assert len(list(incoming.glob("*.parquet"))) == 1


def test_missing_directory_is_a_no_op(warehouse, tmp_path):
    result = import_minute_bars(warehouse, tmp_path / "nope")
    assert result.files == 0
    assert result.rows == 0


def test_data_spanning_two_years_lands_in_both_partitions(warehouse, tmp_path):
    incoming = tmp_path / "in"
    write_incoming(incoming, "a.parquet", start=datetime(2023, 12, 29, 14, 58))
    write_incoming(incoming, "b.parquet", start=datetime(2024, 1, 2, 9, 31))
    import_minute_bars(warehouse, incoming)
    q = WarehouseQuery(warehouse)
    assert q.minute_bars(None, datetime(2023, 1, 1), datetime(2023, 12, 31)).height == 3
    assert q.minute_bars(None, datetime(2024, 1, 1), datetime(2024, 12, 31)).height == 3


def test_a_second_batch_does_not_drop_the_first(warehouse, tmp_path):
    """增量导入必须追加，不是覆盖。"""
    write_incoming(tmp_path / "in1", "a.parquet", symbol="600036.SH")
    import_minute_bars(warehouse, tmp_path / "in1")
    write_incoming(tmp_path / "in2", "b.parquet", symbol="000001.SZ")
    import_minute_bars(warehouse, tmp_path / "in2")
    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 1), datetime(2024, 1, 3)
    )
    assert df["symbol"].n_unique() == 2
    assert df.height == 6
