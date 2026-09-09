"""分红送配导入：与股本/分钟线同构，传输无关、幂等。"""

from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qsys.data import schema
from qsys.data.pipeline.dividends import DIVIDEND_SCHEMA, import_dividends
from qsys.data.query import WarehouseQuery


def write_incoming(directory, name, symbol="600036.SH", rows=2):
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "symbol": [symbol] * rows,
                "ex_date": [date(2024, 6, 20), date(2025, 7, 11)][:rows],
                "cash_per_share": [1.5, 2.0][:rows],
                "stock_per_share": [0.0, 0.3][:rows],
                "rights_per_share": [0.0, 0.0][:rows],
                "rights_price": [0.0, 0.0][:rows],
                "factor": [1.03, 1.35][:rows],
            },
            schema=DIVIDEND_SCHEMA,
        ),
        directory / name,
    )


def test_imports_into_corporate_actions(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet")
    result = import_dividends(warehouse, tmp_path / "in")
    assert result.rows == 2
    df = WarehouseQuery(warehouse).corporate_actions()
    assert df.height == 2
    assert df.columns == list(schema.CORPORATE_ACTIONS.names)


def test_cash_and_stock_are_carried_through(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet")
    import_dividends(warehouse, tmp_path / "in")
    df = WarehouseQuery(warehouse).corporate_actions().sort("ex_date")
    row = df.row(1, named=True)
    assert row["cash_per_share"] == pytest.approx(2.0)
    assert row["stock_per_share"] == pytest.approx(0.3)


def test_rights_are_carried_through(warehouse, tmp_path):
    """配股是 akshare 源没有的——它做不到，QMT 可以。"""
    incoming = tmp_path / "in"
    incoming.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "symbol": ["600036.SH"], "ex_date": [date(2024, 3, 1)],
                "cash_per_share": [0.0], "stock_per_share": [0.0],
                "rights_per_share": [0.3], "rights_price": [8.5],
                "factor": [1.1],
            },
            schema=DIVIDEND_SCHEMA,
        ),
        incoming / "a.parquet",
    )
    import_dividends(warehouse, incoming)
    row = WarehouseQuery(warehouse).corporate_actions().row(0, named=True)
    assert row["rights_per_share"] == pytest.approx(0.3)
    assert row["rights_price"] == pytest.approx(8.5)


def test_reimport_is_idempotent(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet")
    import_dividends(warehouse, tmp_path / "in")
    write_incoming(tmp_path / "in", "a.parquet")
    import_dividends(warehouse, tmp_path / "in")
    assert WarehouseQuery(warehouse).corporate_actions().height == 2


def test_a_second_batch_appends(warehouse, tmp_path):
    write_incoming(tmp_path / "in1", "a.parquet", symbol="600036.SH")
    import_dividends(warehouse, tmp_path / "in1")
    write_incoming(tmp_path / "in2", "b.parquet", symbol="000001.SZ")
    import_dividends(warehouse, tmp_path / "in2")
    df = WarehouseQuery(warehouse).corporate_actions()
    assert df["symbol"].n_unique() == 2
    assert df.height == 4


def test_a_bad_file_does_not_abort_the_batch(warehouse, tmp_path):
    incoming = tmp_path / "in"
    write_incoming(incoming, "good.parquet")
    (incoming / "bad.parquet").write_bytes(b"nope")
    result = import_dividends(warehouse, incoming)
    assert result.rows == 2
    assert result.errors == 1


def test_missing_directory_is_a_no_op(warehouse, tmp_path):
    result = import_dividends(warehouse, tmp_path / "nope")
    assert result.files == 0
