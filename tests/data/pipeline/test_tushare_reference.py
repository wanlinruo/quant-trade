"""Tushare 参考事实表：涨跌停、停牌、指数、标的、名称与涨停列表。"""

from datetime import date

import polars as pl
from qsys.data.pipeline.tushare_reference import (
    ingest_index_daily,
    ingest_instruments,
    ingest_limit_list,
    ingest_name_history,
    ingest_price_limits,
    ingest_suspensions,
    st_on,
)
from qsys.data.query import WarehouseQuery

DAY = date(2026, 9, 11)


class FakeProvider:
    def __init__(self, **tables):
        self.tables = tables
        self.calls = []

    def stk_limit(self, trade_date):
        self.calls.append(("stk_limit", trade_date))
        return self.tables.get("stk_limit", pl.DataFrame())

    def suspend_d(self, trade_date):
        self.calls.append(("suspend_d", trade_date))
        return self.tables.get("suspend_d", pl.DataFrame())

    def index_daily(self, ts_code, start, end):
        self.calls.append(("index_daily", ts_code, start, end))
        return self.tables.get("index_daily", pl.DataFrame())

    def stock_basic(self, list_status):
        self.calls.append(("stock_basic", list_status))
        return self.tables.get(f"stock_basic_{list_status}", pl.DataFrame())

    def namechange(self):
        self.calls.append(("namechange",))
        return self.tables.get("namechange", pl.DataFrame())

    def limit_list_d(self, trade_date, limit_type):
        self.calls.append(("limit_list_d", trade_date, limit_type))
        return self.tables.get("limit_list_d", pl.DataFrame())


def test_price_limits_are_direct_and_idempotent(warehouse):
    provider = FakeProvider(stk_limit=pl.DataFrame({
        "ts_code": ["000001.SZ", "300750.SZ"],
        "trade_date": ["20260911", "20260911"],
        "pre_close": [11.70, 250.0], "up_limit": [12.87, 300.0],
        "down_limit": [10.53, 200.0],
    }))
    assert ingest_price_limits(provider, warehouse, [DAY]) == 2
    assert ingest_price_limits(provider, warehouse, [DAY]) == 2
    got = WarehouseQuery(warehouse).price_limits(None, DAY, DAY).sort("symbol")
    assert got.height == 2
    assert got["limit_up"].to_list() == [12.87, 300.0]
    assert got["limit_down"].to_list() == [10.53, 200.0]
    assert got["pre_close"].to_list() == [11.70, 250.0]


def test_price_limits_get_pre_close_from_tushare_daily_when_api_omits_it(warehouse):
    provider = FakeProvider(
        stk_limit=pl.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": ["20260911"],
            "up_limit": [12.87], "down_limit": [10.53],
        }),
        daily=pl.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": ["20260911"],
            "pre_close": [11.70],
        }),
    )
    provider.daily = lambda _day: provider.tables["daily"]
    ingest_price_limits(provider, warehouse, [DAY])
    assert WarehouseQuery(warehouse).price_limits(None, DAY, DAY)["pre_close"].item() == 11.70


def test_empty_reference_days_write_nothing(warehouse):
    provider = FakeProvider()
    assert ingest_price_limits(provider, warehouse, [DAY]) == 0
    assert ingest_suspensions(provider, warehouse, [DAY]) == 0
    assert ingest_limit_list(provider, warehouse, [date(2019, 1, 2)]) == 0


def test_suspensions_keep_type_and_nullable_timing(warehouse):
    provider = FakeProvider(suspend_d=pl.DataFrame({
        "ts_code": ["000001.SZ", "600000.SH"],
        "trade_date": ["20260911", "20260911"],
        "suspend_type": ["S", "R"], "suspend_timing": ["盘中停牌", None],
    }))
    assert ingest_suspensions(provider, warehouse, [DAY]) == 2
    got = WarehouseQuery(warehouse).suspensions(None, DAY, DAY).sort("symbol")
    assert got["suspend_type"].to_list() == ["S", "R"]
    assert got["suspend_timing"].to_list() == ["盘中停牌", None]


def _index_frame(volume=120_000_000.0):
    return pl.DataFrame({
        "ts_code": ["000300.SH"], "trade_date": ["20260911"],
        "open": [4000.0], "high": [4050.0], "low": [3990.0],
        "close": [4020.0], "pre_close": [3995.0], "vol": [volume],
        "amount": [1_500_000.0],
    })


def test_index_daily_writes_existing_schema_and_converts_amount(warehouse):
    provider = FakeProvider(index_daily=_index_frame())
    assert ingest_index_daily(provider, warehouse, ["000300.SH"], DAY, DAY) == 1
    got = WarehouseQuery(warehouse).index_bars(["000300.SH"], DAY, DAY)
    assert got["close"].to_list() == [4020.0]
    assert got["amount"].to_list() == [1_500_000_000.0]


def test_fractional_index_volume_is_preserved(warehouse):
    provider = FakeProvider(index_daily=_index_frame(123456.78))
    ingest_index_daily(provider, warehouse, ["000300.SH"], DAY, DAY)
    got = WarehouseQuery(warehouse).index_bars(["000300.SH"], DAY, DAY)
    assert got["volume"].to_list() == [123456.78]


def _basic_frame(codes, status):
    return pl.DataFrame({
        "ts_code": codes, "name": [f"股票{i}" for i in range(len(codes))],
        "list_date": ["20100101"] * len(codes),
        "delist_date": ([None] * len(codes) if status == "L"
                        else ["20240601"] * len(codes)),
    })


def test_instruments_include_delisted_and_use_shared_board_mapping(warehouse):
    provider = FakeProvider(
        stock_basic_L=pl.DataFrame({
            "ts_code": ["300750.SZ"], "name": ["宁德时代"],
            "list_date": ["20180611"], "delist_date": [None],
        }),
        stock_basic_D=_basic_frame(["600001.SH"], "D"),
    )
    assert ingest_instruments(provider, warehouse) == 2
    got = WarehouseQuery(warehouse).instruments().sort("symbol")
    assert set(got["symbol"].to_list()) == {"300750.SZ", "600001.SH"}
    assert got.filter(pl.col("symbol") == "300750.SZ")["board"].item() == "gem"
    assert got.filter(pl.col("symbol") == "600001.SH")["delist_date"].item() == date(2024, 6, 1)


def _name_history_raw():
    return pl.DataFrame({
        "ts_code": ["000010.SZ", "000010.SZ"],
        "name": ["*ST美丽", "美丽生态"],
        "start_date": ["20260430", "20200722"],
        "end_date": [None, "20260429"],
        "ann_date": ["20260429", "20200721"],
        "change_reason": ["*ST", "撤销*ST"],
    })


def test_name_history_and_st_interval_semantics(warehouse):
    assert ingest_name_history(FakeProvider(namechange=_name_history_raw()), warehouse) == 2
    history = WarehouseQuery(warehouse).name_history(["000010.SZ"])
    before = st_on(history, date(2026, 4, 29))
    after = st_on(history, date(2026, 4, 30))
    assert before.select("is_st", "name").row(0) == (False, "美丽生态")
    assert after.select("is_st", "name").row(0) == (True, "*ST美丽")
    assert st_on(history, date(2019, 1, 1)).height == 0


def test_st_detection_is_prefix_only():
    history = pl.DataFrame({
        "symbol": ["000078.SZ", "000079.SZ"],
        "name": ["ST海王", "BEST公司"],
        "start_date": [DAY, DAY], "end_date": [None, None],
        "ann_date": [DAY, DAY], "change_reason": ["ST", "更名"],
    })
    assert st_on(history, DAY)["is_st"].to_list() == [True, False]


def test_limit_list_keeps_direct_board_statistics(warehouse):
    provider = FakeProvider(limit_list_d=pl.DataFrame({
        "ts_code": ["000001.SZ", "600000.SH"],
        "trade_date": ["20260911", "20260911"],
        "limit_times": [1, 3], "open_times": [0, 2],
        "first_time": ["09:31:00", "10:15:00"],
        "fd_amount": [1.2e8, 3.4e7], "limit_amount": [5.0e8, 6.0e8],
        "float_mv": [1.0e10, 2.0e10],
    }))
    assert ingest_limit_list(provider, warehouse, [DAY]) == 2
    got = WarehouseQuery(warehouse).limit_list(None, DAY, DAY).sort("symbol")
    assert got["limit_times"].to_list() == [1, 3]
    assert got["open_times"].to_list() == [0, 2]
    assert got["limit_type"].to_list() == ["U", "U"]
