from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qsys.data import schema
from qsys.data.storage import Warehouse


def _bars(close: float) -> pa.Table:
    return pa.table(
        {
            "symbol": ["600036.SH"],
            "dt": [date(2024, 1, 2)],
            "open": [30.0],
            "high": [31.0],
            "low": [29.5],
            "close": [close],
            "volume": [1000],
            "amount": [30500.0],
        },
        schema=schema.BARS_DAILY,
    )


def test_write_creates_partitioned_file(warehouse: Warehouse):
    path = warehouse.write("bars_daily", _bars(30.5), year=2024)
    assert path.exists()
    assert path == warehouse.root / "bars_daily" / "year=2024" / "part.parquet"


def test_write_is_idempotent_overwrite(warehouse: Warehouse):
    warehouse.write("bars_daily", _bars(30.5), year=2024)
    path = warehouse.write("bars_daily", _bars(31.5), year=2024)
    table = pq.read_table(path)
    assert table.num_rows == 1
    assert table.column("close").to_pylist() == [31.5]


def test_write_leaves_no_temp_files(warehouse: Warehouse):
    warehouse.write("bars_daily", _bars(30.5), year=2024)
    leftovers = list((warehouse.root / "bars_daily" / "year=2024").glob("*.tmp*"))
    assert leftovers == []


def test_write_rejects_schema_mismatch(warehouse: Warehouse):
    wrong = pa.table({"symbol": ["600036.SH"]})
    with pytest.raises(ValueError, match="schema mismatch"):
        warehouse.write("bars_daily", wrong, year=2024)


def test_write_unpartitioned_dataset(warehouse: Warehouse):
    table = pa.table(
        {
            "symbol": ["600036.SH"],
            "name": ["招商银行"],
            "market": ["SH"],
            "list_date": [date(2002, 4, 9)],
            "delist_date": [None],
            "board": ["main"],
        },
        schema=schema.INSTRUMENTS,
    )
    path = warehouse.write("instruments", table)
    assert path == warehouse.root / "instruments" / "part.parquet"


def test_write_rejects_unknown_dataset(warehouse: Warehouse):
    with pytest.raises(KeyError, match="unknown dataset"):
        warehouse.write("nope", _bars(1.0), year=2024)
