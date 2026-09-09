"""集合竞价 tick 导入。"""

from datetime import date, datetime

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.pipeline.auction import (
    ImportResult,
    auction_at,
    first_auction_record,
    import_auction_ticks,
    missing_auction_days,
)
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

DAY = date(2026, 9, 8)


def tick(symbol: str, hhmmss: str, bid1: float, vol: int = 100, dt: date = DAY) -> dict:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return {
        "symbol": symbol,
        "dt": dt,
        "ts": datetime(dt.year, dt.month, dt.day, h, m, s),
        "bid1": bid1,
        "ask1": bid1,
        "bid_vol1": vol,
        "ask_vol1": vol,
        "last_price": 0.0,
        "volume": 0,
        "amount": 0.0,
    }


def write_incoming(directory, rows, name="auction_00000.parquet"):
    directory.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).select(list(schema.AUCTION_TICKS.names))
    frame.write_parquet(directory / name)
    return directory / name


def test_import_writes_rows_and_archives(warehouse, tmp_path):
    incoming, archive = tmp_path / "in", tmp_path / "done"
    write_incoming(incoming, [tick("600036.SH", "09:15:03", 41.07)])

    result = import_auction_ticks(warehouse, incoming, archive_dir=archive)

    assert result == ImportResult(files=1, rows=1, errors=0)
    assert list(incoming.glob("*.parquet")) == []
    assert len(list(archive.glob("*.parquet"))) == 1


def test_import_returns_zero_when_directory_missing(warehouse, tmp_path):
    assert import_auction_ticks(warehouse, tmp_path / "nope") == ImportResult(0, 0, 0)


def test_one_bad_file_does_not_sink_the_batch(warehouse, tmp_path):
    incoming = tmp_path / "in"
    write_incoming(incoming, [tick("600036.SH", "09:15:03", 41.07)])
    incoming.joinpath("auction_00001.parquet").write_bytes(b"not a parquet")

    result = import_auction_ticks(warehouse, incoming)

    assert result.rows == 1
    assert result.errors == 1


def test_reimport_of_the_same_moment_overwrites(warehouse, tmp_path):
    write_incoming(tmp_path / "a", [tick("600036.SH", "09:15:03", 41.07)])
    import_auction_ticks(warehouse, tmp_path / "a")
    write_incoming(tmp_path / "b", [tick("600036.SH", "09:15:03", 41.99)])
    import_auction_ticks(warehouse, tmp_path / "b")

    got = WarehouseQuery(warehouse).auction_ticks(None, DAY, DAY)

    assert got.height == 1
    assert got["bid1"][0] == 41.99


def test_partial_import_keeps_the_rest_of_the_year(warehouse, tmp_path):
    """同年再导一天，不能抹掉先前那天——分区是按年写的。"""
    other = date(2026, 9, 7)
    write_incoming(tmp_path / "a", [tick("600036.SH", "09:15:03", 41.07, dt=other)])
    import_auction_ticks(warehouse, tmp_path / "a")
    write_incoming(tmp_path / "b", [tick("600036.SH", "09:15:03", 42.00)])
    import_auction_ticks(warehouse, tmp_path / "b")

    got = WarehouseQuery(warehouse).auction_ticks(None, other, DAY)

    assert sorted(got["dt"].to_list()) == [other, DAY]


def test_missing_auction_days_reports_what_is_absent(warehouse, tmp_path):
    write_incoming(tmp_path / "a", [tick("600036.SH", "09:15:03", 41.07)])
    import_auction_ticks(warehouse, tmp_path / "a")

    wanted = pl.DataFrame(
        {"symbol": ["600036.SH", "000001.SZ"], "dt": [DAY, DAY]},
        schema={"symbol": pl.Utf8, "dt": pl.Date},
    )

    assert missing_auction_days(warehouse, wanted)["symbol"].to_list() == ["000001.SZ"]


def test_reader_returns_typed_empty_before_any_import(warehouse):
    got = WarehouseQuery(warehouse).auction_ticks(None, DAY, DAY)

    assert got.height == 0
    assert set(schema.AUCTION_TICKS.names) <= set(got.columns)


# ---- 两种口径都从同一份原始快照算出来 ----

def _seed(warehouse, tmp_path):
    write_incoming(
        tmp_path / "in",
        [
            # 09:15:00 整行是 0：集合竞价刚开始，还没有委托可撮合
            tick("600036.SH", "09:15:00", 0.0, vol=0),
            tick("600036.SH", "09:17:30", 41.07, vol=601),
            tick("600036.SH", "09:21:00", 41.05, vol=626),
            tick("600036.SH", "09:25:00", 41.01, vol=2195),
        ],
    )
    import_auction_ticks(warehouse, tmp_path / "in")
    return WarehouseQuery(warehouse)


def test_first_auction_record_skips_the_all_zero_opening_snapshot(warehouse, tmp_path):
    query = _seed(warehouse, tmp_path)

    got = first_auction_record(query, DAY, ["600036.SH"])["600036.SH"]

    assert got["current"] == 41.07
    assert got["volume"] == 601


def test_auction_at_takes_the_last_snapshot_up_to_the_moment(warehouse, tmp_path):
    query = _seed(warehouse, tmp_path)

    got = auction_at(
        query, DAY, ["600036.SH"], datetime(2026, 9, 8, 9, 22)
    )["600036.SH"]

    assert got["current"] == 41.05
    assert got["volume"] == 626


def test_auction_at_before_any_snapshot_returns_nothing(warehouse, tmp_path):
    query = _seed(warehouse, tmp_path)

    assert auction_at(query, DAY, ["600036.SH"], datetime(2026, 9, 8, 9, 16)) == {}


@pytest.mark.parametrize("fn", [first_auction_record, auction_at])
def test_lookups_on_an_empty_warehouse_return_nothing(warehouse, fn):
    query = WarehouseQuery(warehouse)
    args = (query, DAY, ["600036.SH"])
    if fn is auction_at:
        args = (*args, datetime(2026, 9, 8, 9, 25))

    assert fn(*args) == {}
