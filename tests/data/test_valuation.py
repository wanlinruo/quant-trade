"""估值快照：全市场一次调用，历史从采集之日起累积。"""

from datetime import date

import pandas as pd
import polars as pl
import pytest

from qsys.data.pipeline.valuation import ingest_valuation_snapshot
from qsys.data.providers.akshare_provider import AkshareProvider
from qsys.data.query import WarehouseQuery

D = date(2024, 1, 3)


class FakeApi:
    def spot(self):
        return pd.DataFrame(
            {
                "代码": ["600036", "300750", "123456"],
                "最新价": [33.18, 191.60, 5.0],
                "总市值": [8.36e11, 8.43e11, 1e9],
                "流通市值": [6.83e11, 7.30e11, 1e9],
                "换手率": [0.51, 0.83, 1.2],
            }
        )


def test_snapshot_columns():
    df = AkshareProvider(api=FakeApi()).fetch_valuation_snapshot()
    assert df.columns == [
        "symbol",
        "close",
        "market_cap",
        "circulating_market_cap",
        "turnover_ratio",
    ]


def test_snapshot_normalises_symbols():
    df = AkshareProvider(api=FakeApi()).fetch_valuation_snapshot()
    assert "600036.SH" in df["symbol"].to_list()
    assert "300750.SZ" in df["symbol"].to_list()


def test_snapshot_skips_unknown_code_segments():
    """交易所新号段不该让整次采集失败。"""
    df = AkshareProvider(api=FakeApi()).fetch_valuation_snapshot()
    assert df.height == 2


def test_market_cap_is_in_yuan_not_billions():
    df = AkshareProvider(api=FakeApi()).fetch_valuation_snapshot()
    row = df.filter(pl.col("symbol") == "600036.SH").row(0, named=True)
    assert row["market_cap"] == pytest.approx(8.36e11)


def test_ingest_writes_the_snapshot(warehouse):
    n = ingest_valuation_snapshot(AkshareProvider(api=FakeApi()), warehouse, D)
    assert n == 2
    df = WarehouseQuery(warehouse).valuation(D, D)
    assert df.height == 2
    assert df["dt"].to_list() == [D, D]


def test_ingest_is_idempotent(warehouse):
    p = AkshareProvider(api=FakeApi())
    ingest_valuation_snapshot(p, warehouse, D)
    ingest_valuation_snapshot(p, warehouse, D)
    assert WarehouseQuery(warehouse).valuation(D, D).height == 2


def test_different_days_accumulate(warehouse):
    p = AkshareProvider(api=FakeApi())
    ingest_valuation_snapshot(p, warehouse, D)
    ingest_valuation_snapshot(p, warehouse, date(2024, 1, 4))
    assert WarehouseQuery(warehouse).valuation().height == 4


def test_missing_values_do_not_crash_the_snapshot(warehouse):
    """停牌股的市值字段常为空，不该让整次采集失败。"""

    class NullApi(FakeApi):
        def spot(self):
            return pd.DataFrame(
                {
                    "代码": ["600036"],
                    "最新价": [None],
                    "总市值": [None],
                    "流通市值": [None],
                    "换手率": [None],
                }
            )

    df = AkshareProvider(api=NullApi()).fetch_valuation_snapshot()
    assert df.height == 1
    assert df.row(0, named=True)["market_cap"] == 0.0


def test_empty_snapshot_is_a_no_op(warehouse):
    class EmptyApi(FakeApi):
        def spot(self):
            return pd.DataFrame()

    assert ingest_valuation_snapshot(AkshareProvider(api=EmptyApi()), warehouse, D) == 0
