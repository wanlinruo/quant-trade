"""股本导入：与分钟线同构，传输无关、幂等。"""

from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.pipeline.capital import import_share_capital
from qsys.data.query import WarehouseQuery


def write_incoming(directory, name, symbol="600036.SH", rows=2):
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "symbol": [symbol] * rows,
                "dt": [date(2023, 3, 31), date(2023, 6, 30)][:rows],
                "ann_date": [date(2023, 4, 2), date(2023, 7, 2)][:rows],
                "total": [1.0e10, 1.2e10][:rows],
                "circulating": [0.8e10, 0.9e10][:rows],
            },
            schema=schema.SHARE_CAPITAL,
        ),
        directory / name,
    )


def test_imports_a_file(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet")
    result = import_share_capital(warehouse, tmp_path / "in")
    assert result.files == 1
    assert result.rows == 2
    assert WarehouseQuery(warehouse).share_capital().height == 2


def test_reimport_is_idempotent(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet")
    import_share_capital(warehouse, tmp_path / "in")
    write_incoming(tmp_path / "in", "a.parquet")
    import_share_capital(warehouse, tmp_path / "in")
    assert WarehouseQuery(warehouse).share_capital().height == 2


def test_a_second_batch_appends(warehouse, tmp_path):
    write_incoming(tmp_path / "in1", "a.parquet", symbol="600036.SH")
    import_share_capital(warehouse, tmp_path / "in1")
    write_incoming(tmp_path / "in2", "b.parquet", symbol="000001.SZ")
    import_share_capital(warehouse, tmp_path / "in2")
    df = WarehouseQuery(warehouse).share_capital()
    assert df["symbol"].n_unique() == 2
    assert df.height == 4


def test_a_bad_file_does_not_abort_the_batch(warehouse, tmp_path):
    incoming = tmp_path / "in"
    write_incoming(incoming, "good.parquet")
    (incoming / "bad.parquet").write_bytes(b"not parquet")
    result = import_share_capital(warehouse, incoming)
    assert result.rows == 2
    assert result.errors == 1


def test_missing_directory_is_a_no_op(warehouse, tmp_path):
    result = import_share_capital(warehouse, tmp_path / "nope")
    assert result.files == 0
    assert result.rows == 0


def test_archived_files_avoid_name_collisions(warehouse, tmp_path):
    incoming, archive = tmp_path / "in", tmp_path / "done"
    write_incoming(incoming, "capital_00000.parquet", symbol="600036.SH")
    import_share_capital(warehouse, incoming, archive_dir=archive)
    write_incoming(incoming, "capital_00000.parquet", symbol="000001.SZ")
    import_share_capital(warehouse, incoming, archive_dir=archive)
    assert len(list(archive.glob("*.parquet"))) == 2


def test_updating_a_record_keeps_the_new_value(warehouse, tmp_path):
    """同一 (symbol, 生效日) 重采要以新值为准。"""
    incoming = tmp_path / "in"
    write_incoming(incoming, "a.parquet", rows=1)
    import_share_capital(warehouse, incoming)

    incoming.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "symbol": ["600036.SH"],
                "dt": [date(2023, 3, 31)],
                "ann_date": [date(2023, 4, 2)],
                "total": [9.9e10],
                "circulating": [8.8e10],
            },
            schema=schema.SHARE_CAPITAL,
        ),
        incoming / "b.parquet",
    )
    import_share_capital(warehouse, incoming)
    df = WarehouseQuery(warehouse).share_capital()
    assert df.height == 1
    assert df.row(0, named=True)["total"] == 9.9e10
