"""指数日线：沪深300 是 S6 Gate 的输入，个股接口取不到，须走 index()。"""

from datetime import date

import pandas as pd
import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.pipeline.index_bars import ingest_index_bars
from qsys.data.providers.mootdx_provider import MootdxProvider
from qsys.data.query import WarehouseQuery


class FakeIndexClient:
    """mootdx 指数接口：只有 index() 有数据，bars() 对指数返回空。"""

    def __init__(self, rows=3):
        self.index_calls: list[dict] = []
        self.rows = rows

    def index(self, symbol, frequency, start=0, offset=800):
        self.index_calls.append(
            {"symbol": symbol, "frequency": frequency, "start": start, "offset": offset}
        )
        if start > 0:
            return pd.DataFrame()
        n = self.rows
        return pd.DataFrame(
            {
                "datetime": [f"2024-01-0{i + 2} 15:00" for i in range(n)],
                "open": [3800.0] * n,
                "high": [3850.0] * n,
                "low": [3780.0] * n,
                "close": [3820.0 + i for i in range(n)],
                "vol": [1000.0] * n,
                "amount": [1e10] * n,
            }
        )

    def bars(self, symbol, frequency, start=0, offset=800):
        return pd.DataFrame()


def test_index_bars_use_the_index_endpoint():
    client = FakeIndexClient()
    df = MootdxProvider(client=client).fetch_index_bars(
        "000300.SH", date(2024, 1, 1), date(2024, 1, 31)
    )
    assert client.index_calls, "指数必须走 index()，bars() 对指数返回空"
    assert df.height == 3


def test_index_bars_are_normalised_like_daily_bars():
    df = MootdxProvider(client=FakeIndexClient()).fetch_index_bars(
        "000300.SH", date(2024, 1, 1), date(2024, 1, 31)
    )
    assert df.columns == ["dt", "open", "high", "low", "close", "volume", "amount"]
    assert df["dt"].to_list()[0] == date(2024, 1, 2)


def test_index_bars_are_clipped_to_the_range():
    df = MootdxProvider(client=FakeIndexClient()).fetch_index_bars(
        "000300.SH", date(2024, 1, 3), date(2024, 1, 3)
    )
    assert df["dt"].to_list() == [date(2024, 1, 3)]


def test_index_bars_page_like_daily_bars():
    client = FakeIndexClient()
    MootdxProvider(client=client, page_size=800).fetch_index_bars(
        "000300.SH", date(2020, 1, 1), date(2024, 1, 31)
    )
    assert [c["start"] for c in client.index_calls[:2]] == [0, 800]


def test_ingest_writes_index_bars(warehouse):
    n = ingest_index_bars(
        MootdxProvider(client=FakeIndexClient()),
        warehouse,
        ["000300.SH"],
        date(2024, 1, 1),
        date(2024, 1, 31),
        complete_through=date(2024, 1, 31),
    )
    assert n == 3
    df = WarehouseQuery(warehouse).index_bars(None, date(2024, 1, 1), date(2024, 1, 31))
    assert df.height == 3
    assert df["symbol"].to_list() == ["000300.SH"] * 3
    assert WarehouseQuery(warehouse).dataset_bounds("index_bars") == (
        date(2024, 1, 2), date(2024, 1, 4))


def test_ingest_is_idempotent(warehouse):
    provider = MootdxProvider(client=FakeIndexClient())
    ingest_index_bars(provider, warehouse, ["000300.SH"], date(2024, 1, 1),
                      date(2024, 1, 31), complete_through=date(2024, 1, 31))
    ingest_index_bars(provider, warehouse, ["000300.SH"], date(2024, 1, 1),
                      date(2024, 1, 31), complete_through=date(2024, 1, 31))
    assert WarehouseQuery(warehouse).index_bars().height == 3


def test_unclosed_sessions_are_dropped(warehouse):
    """与日线同一条纪律：未收盘的当日 bar 不落盘。"""
    ingest_index_bars(
        MootdxProvider(client=FakeIndexClient()), warehouse, ["000300.SH"],
        date(2024, 1, 1), date(2024, 1, 31), complete_through=date(2024, 1, 3),
    )
    df = WarehouseQuery(warehouse).index_bars()
    assert df["dt"].max() == date(2024, 1, 3)


def test_index_bars_on_empty_warehouse(warehouse):
    df = WarehouseQuery(warehouse).index_bars()
    assert df.height == 0
    assert df.columns == list(schema.INDEX_BARS.names)
