from datetime import date

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.pipeline.first_board_inputs import (
    ingest_auction_open,
    ingest_industry_membership,
    ingest_moneyflow,
    validate_industry_intervals,
    verify_first_board_asset_health,
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
        "buy_sm_vol": [10.0], "sell_sm_vol": [20.0],
        "buy_sm_amount": [1.0], "sell_sm_amount": [2.0],
        "buy_md_vol": [30.0], "sell_md_vol": [40.0],
        "buy_md_amount": [3.0], "sell_md_amount": [4.0],
        "buy_lg_vol": [50.0], "sell_lg_vol": [60.0],
        "buy_lg_amount": [5.0], "sell_lg_amount": [6.0],
        "buy_elg_vol": [70.0], "sell_elg_vol": [80.0],
        "buy_elg_amount": [7.0], "sell_elg_amount": [8.0],
        "net_mf_vol": [-90.0], "net_mf_amount": [-4.0],
    }
    assert ingest_moneyflow(Provider(moneyflow=pl.DataFrame(row)), warehouse, [DAY]) == 1
    got = WarehouseQuery(warehouse).moneyflow(None, DAY, DAY).row(0, named=True)
    assert got["buy_sm_amount"] == 10_000.0
    assert got["buy_sm_vol"] == 10.0
    assert got["net_mf_vol"] == -90.0
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


def test_industry_membership_rejects_inverted_or_overlapping_intervals():
    base = {
        "symbol": ["600001.SH", "600001.SH"],
        "l3_code": ["OLD", "NEW"],
        "in_date": [date(2020, 1, 1), date(2024, 1, 1)],
        "out_date": [date(2024, 12, 31), None],
    }
    with pytest.raises(ValueError, match="overlapping"):
        validate_industry_intervals(pl.DataFrame(base))

    inverted = pl.DataFrame({
        "symbol": ["600001.SH"], "l3_code": ["BAD"],
        "in_date": [date(2025, 1, 1)], "out_date": [date(2024, 12, 31)],
    })
    with pytest.raises(ValueError, match="inverted"):
        validate_industry_intervals(inverted)


def test_industry_membership_rejects_duplicate_source_keys(warehouse):
    row = {
        "ts_code": ["600001.SH", "600001.SH"],
        "l1_code": ["A", "A"], "l1_name": ["一级", "一级"],
        "l2_code": ["A1", "A1"], "l2_name": ["二级", "二级"],
        "l3_code": ["A11", "A11"], "l3_name": ["三级", "三级"],
        "in_date": ["20200101", "20200101"],
        "out_date": [None, None], "is_new": ["Y", "Y"],
    }
    with pytest.raises(ValueError, match="duplicate interval keys"):
        ingest_industry_membership(Provider(industry=pl.DataFrame(row)), warehouse)


def test_first_board_health_rejects_a_file_in_quarantined_auction(warehouse):
    money = pl.DataFrame({
        "symbol": ["600001.SH"], "dt": [DAY],
        **{name: [1.0] for name in (
            "buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount",
            "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount",
            "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
            "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount",
            "net_mf_vol", "net_mf_amount",
        )},
    })
    warehouse.write(
        "moneyflow_daily", money.to_arrow().cast(schema.MONEYFLOW_DAILY), year=DAY.year,
    )
    industry = pl.DataFrame({
        "symbol": ["600001.SH"],
        "l1_code": ["A"], "l1_name": ["一级"],
        "l2_code": ["A1"], "l2_name": ["二级"],
        "l3_code": ["A11"], "l3_name": ["三级"],
        "in_date": [date(2020, 1, 1)], "out_date": [None], "is_new": [True],
    })
    warehouse.write(
        "industry_membership", industry.to_arrow().cast(schema.INDUSTRY_MEMBERSHIP),
    )
    assert verify_first_board_asset_health(warehouse, DAY) == 2

    path = warehouse.day_path("auction_open_daily", DAY)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not even parquet")
    with pytest.raises(RuntimeError, match="must remain empty"):
        verify_first_board_asset_health(warehouse, DAY)


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
