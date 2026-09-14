from datetime import date

import polars as pl

from qsys.data.pipeline.bars import ingest_daily_bars
from qsys.data.pipeline.ingest_log import IngestLog
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

BAR_SCHEMA = {
    "dt": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
    "amount": pl.Float64,
}


class FakeProvider:
    name = "fake"

    def __init__(self, data: dict[str, pl.DataFrame], failing: set[str] | None = None):
        self._data = data
        self._failing = failing or set()

    def fetch_daily_bars(self, symbol, start, end):
        if symbol in self._failing:
            raise RuntimeError("connection reset")
        frame = self._data.get(symbol, pl.DataFrame(schema=BAR_SCHEMA))
        return frame.filter(pl.col("dt").is_between(start, end))


def _bars(dates: list[date], close: float) -> pl.DataFrame:
    n = len(dates)
    return pl.DataFrame(
        {
            "dt": dates,
            "open": [close] * n,
            "high": [close + 1] * n,
            "low": [close - 1] * n,
            "close": [close] * n,
            "volume": [1000] * n,
            "amount": [close * 1000] * n,
        },
        schema=BAR_SCHEMA,
    )


def test_ingest_writes_bars(warehouse: Warehouse):
    provider = FakeProvider({"600036.SH": _bars([date(2024, 1, 2), date(2024, 1, 3)], 30.0)})
    written = ingest_daily_bars(
        provider, warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert written == 2
    df = WarehouseQuery(warehouse).legacy_daily_bars(None, date(2024, 1, 1), date(2024, 1, 31))
    assert df.height == 2
    assert df["symbol"].to_list() == ["600036.SH", "600036.SH"]


def test_ingest_is_idempotent(warehouse: Warehouse):
    provider = FakeProvider({"600036.SH": _bars([date(2024, 1, 2)], 30.0)})
    ingest_daily_bars(provider, warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31))
    ingest_daily_bars(provider, warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31))
    assert WarehouseQuery(warehouse).legacy_daily_bars(
        None, date(2024, 1, 1), date(2024, 1, 31)
    ).height == 1


def test_ingest_merges_with_existing_data(warehouse: Warehouse):
    ingest_daily_bars(
        FakeProvider({"600036.SH": _bars([date(2024, 1, 2)], 30.0)}),
        warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31),
    )
    ingest_daily_bars(
        FakeProvider({"000001.SZ": _bars([date(2024, 1, 2)], 10.0)}),
        warehouse, ["000001.SZ"], date(2024, 1, 1), date(2024, 1, 31),
    )
    df = WarehouseQuery(warehouse).legacy_daily_bars(None, date(2024, 1, 1), date(2024, 1, 31))
    assert sorted(df["symbol"].to_list()) == ["000001.SZ", "600036.SH"]


def test_ingest_partitions_by_year(warehouse: Warehouse):
    provider = FakeProvider(
        {"600036.SH": _bars([date(2023, 12, 29), date(2024, 1, 2)], 30.0)}
    )
    ingest_daily_bars(provider, warehouse, ["600036.SH"], date(2023, 1, 1), date(2024, 12, 31))
    assert warehouse.path_for("bars_daily", 2023).exists()
    assert warehouse.path_for("bars_daily", 2024).exists()


def test_ingest_records_success_in_log(warehouse: Warehouse):
    provider = FakeProvider({"600036.SH": _bars([date(2024, 1, 2)], 30.0)})
    ingest_daily_bars(provider, warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31))
    entries = IngestLog(warehouse).entries(2024)
    assert entries.filter(pl.col("ok")).height >= 1


def test_failing_symbol_does_not_abort_others(warehouse: Warehouse):
    provider = FakeProvider(
        {"000001.SZ": _bars([date(2024, 1, 2)], 10.0)}, failing={"600036.SH"}
    )
    written = ingest_daily_bars(
        provider, warehouse, ["600036.SH", "000001.SZ"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert written == 1
    df = WarehouseQuery(warehouse).legacy_daily_bars(None, date(2024, 1, 1), date(2024, 1, 31))
    assert df["symbol"].to_list() == ["000001.SZ"]


def test_failure_is_recorded_in_log(warehouse: Warehouse):
    provider = FakeProvider({}, failing={"600036.SH"})
    ingest_daily_bars(provider, warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31))
    entries = IngestLog(warehouse).entries(2024)
    failed = entries.filter(~pl.col("ok"))
    assert failed.height == 1
    assert "connection reset" in failed["message"].to_list()[0]


def test_ingest_with_no_data_writes_nothing(warehouse: Warehouse):
    written = ingest_daily_bars(
        FakeProvider({}), warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert written == 0
