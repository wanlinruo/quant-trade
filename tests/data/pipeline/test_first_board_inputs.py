from datetime import date

import polars as pl
import pytest

from qsys.data.pipeline.first_board_inputs import (
    ingest_auction_open,
    ingest_industry_membership,
    ingest_moneyflow,
)
from qsys.data.query import WarehouseQuery


DAY = date(2026, 9, 11)


class Provider:
    def __init__(self, *, moneyflow=None, industry=None, auction=None):
        self._moneyflow = moneyflow if moneyflow is not None else pl.DataFrame()
        self._industry = industry if industry is not None else pl.DataFrame()
        self._auction = auction if auction is not None else pl.DataFrame()

    def moneyflow(self, _day):
        return self._moneyflow

    def industry_membership(self):
        return self._industry

    def auction_open(self, _day):
        return self._auction


def test_moneyflow_normalizes_amounts_and_replaces_day(warehouse):
    row = {
        "ts_code": ["600001.SH"], "trade_date": ["20260911"],
        "buy_sm_amount": [1.0], "sell_sm_amount": [2.0],
        "buy_md_amount": [3.0], "sell_md_amount": [4.0],
        "buy_lg_amount": [5.0], "sell_lg_amount": [6.0],
        "buy_elg_amount": [7.0], "sell_elg_amount": [8.0],
        "net_mf_amount": [-4.0],
    }
    assert ingest_moneyflow(Provider(moneyflow=pl.DataFrame(row)), warehouse, [DAY]) == 1
    got = WarehouseQuery(warehouse).moneyflow(None, DAY, DAY).row(0, named=True)
    assert got["buy_sm_amount"] == 10_000.0
    assert got["net_mf_amount"] == -40_000.0

    replacement = {**row, "buy_sm_amount": [9.0]}
    ingest_moneyflow(Provider(moneyflow=pl.DataFrame(replacement)), warehouse, [DAY])
    got = WarehouseQuery(warehouse).moneyflow(None, DAY, DAY)
    assert got.height == 1
    assert got["buy_sm_amount"].item() == 90_000.0


def test_empty_moneyflow_fails_closed(warehouse):
    with pytest.raises(RuntimeError, match="moneyflow"):
        ingest_moneyflow(Provider(), warehouse, [DAY])


def test_industry_membership_resolves_historical_interval(warehouse):
    raw = pl.DataFrame({
        "ts_code": ["600001.SH", "600001.SH"],
        "l1_code": ["A", "B"], "l1_name": ["旧行业", "新行业"],
        "l2_code": ["A1", "B1"], "l2_name": ["旧二级", "新二级"],
        "l3_code": ["A11", "B11"], "l3_name": ["旧三级", "新三级"],
        "in_date": ["20200101", "20250101"],
        "out_date": ["20241231", None], "is_new": ["N", "Y"],
    })
    assert ingest_industry_membership(Provider(industry=raw), warehouse) == 2
    query = WarehouseQuery(warehouse)
    assert query.industry_membership(as_of=date(2024, 6, 1))["l1_name"].to_list() == [
        "旧行业"
    ]
    assert query.industry_membership(as_of=date(2026, 1, 1))["l1_name"].to_list() == [
        "新行业"
    ]


def test_auction_stays_source_native_for_parity_study(warehouse):
    raw = pl.DataFrame({
        "ts_code": ["600001.SH"], "trade_date": ["20260911"],
        "open": [10.0], "close": [10.0], "high": [10.0], "low": [10.0],
        "vol": [1234.0], "amount": [12_340.0], "vwap": [10.0],
    })
    assert ingest_auction_open(Provider(auction=raw), warehouse, [DAY]) == 1
    got = WarehouseQuery(warehouse).auction_open(None, DAY, DAY).row(0, named=True)
    assert got["volume_raw"] == 1234.0
    assert got["amount_raw"] == 12_340.0
