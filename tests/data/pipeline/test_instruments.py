from datetime import date

import polars as pl

from qsys.data.pipeline.instruments import alive_on, build_instruments

# 快照日期显式给定——build_instruments 不内部取 today()，
# 否则结果依赖墙上时钟。
AS_OF = date(2026, 9, 14)
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

_SCHEMA = {
    "symbol": pl.Utf8,
    "name": pl.Utf8,
    "market": pl.Utf8,
    "list_date": pl.Date,
    "delist_date": pl.Date,
    "board": pl.Utf8,
}


class FakeProvider:
    name = "fake"

    def __init__(self, frame: pl.DataFrame) -> None:
        self._frame = frame

    def fetch_instruments(self) -> pl.DataFrame:
        return self._frame

    def fetch_sessions(self, start, end):
        raise NotImplementedError

    def fetch_corporate_actions(self, symbol):
        raise NotImplementedError


def _frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["600036.SH", "000003.SZ", "300750.SZ", "600036.SH"],
            "name": ["招商银行", "PT金田A", "宁德时代", "招商银行"],
            "market": ["SH", "SZ", "SZ", "SH"],
            "list_date": [date(2002, 4, 9), date(1991, 7, 3), date(2018, 6, 11), None],
            "delist_date": [None, date(2002, 6, 14), None, None],
            "board": ["main", "main", "gem", "main"],
        },
        schema=_SCHEMA,
    )


def test_build_writes_deduplicated_instruments(warehouse: Warehouse):
    count = build_instruments(FakeProvider(_frame()), warehouse, AS_OF)
    assert count == 3
    df = WarehouseQuery(warehouse).instruments()
    assert sorted(df["symbol"].to_list()) == ["000003.SZ", "300750.SZ", "600036.SH"]


def test_build_keeps_delisted_instruments(warehouse: Warehouse):
    build_instruments(FakeProvider(_frame()), warehouse, AS_OF)
    df = WarehouseQuery(warehouse).instruments()
    delisted = df.filter(pl.col("delist_date").is_not_null())
    assert delisted["symbol"].to_list() == ["000003.SZ"]


def test_build_is_idempotent(warehouse: Warehouse):
    build_instruments(FakeProvider(_frame()), warehouse, AS_OF)
    build_instruments(FakeProvider(_frame()), warehouse, AS_OF)
    assert WarehouseQuery(warehouse).instruments().height == 3


def test_dedupe_prefers_row_with_more_information(warehouse: Warehouse):
    build_instruments(FakeProvider(_frame()), warehouse, AS_OF)
    df = WarehouseQuery(warehouse).instruments()
    cmb = df.filter(pl.col("symbol") == "600036.SH")
    assert cmb["list_date"].to_list() == [date(2002, 4, 9)]


def test_alive_on_excludes_not_yet_listed():
    df = _frame().unique(subset=["symbol"], keep="first", maintain_order=True)
    alive = alive_on(df, date(2010, 1, 4))
    assert "300750.SZ" not in alive["symbol"].to_list()


def test_alive_on_excludes_already_delisted():
    df = _frame().unique(subset=["symbol"], keep="first", maintain_order=True)
    alive = alive_on(df, date(2010, 1, 4))
    assert "000003.SZ" not in alive["symbol"].to_list()


def test_alive_on_includes_still_listed():
    df = _frame().unique(subset=["symbol"], keep="first", maintain_order=True)
    alive = alive_on(df, date(2010, 1, 4))
    assert alive["symbol"].to_list() == ["600036.SH"]


def test_alive_on_includes_instrument_alive_on_its_delist_date():
    df = _frame().unique(subset=["symbol"], keep="first", maintain_order=True)
    alive = alive_on(df, date(2002, 6, 14))
    assert "000003.SZ" in alive["symbol"].to_list()


def test_alive_on_treats_null_list_date_as_always_listed():
    df = pl.DataFrame(
        {
            "symbol": ["600036.SH"],
            "name": ["招商银行"],
            "market": ["SH"],
            "list_date": [None],
            "delist_date": [None],
            "board": ["main"],
        },
        schema=_SCHEMA,
    )
    assert alive_on(df, date(1990, 1, 1)).height == 1
