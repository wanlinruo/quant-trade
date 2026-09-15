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
    ingest_stock_st,
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

    def stock_st(self, trade_date):
        self.calls.append(("stock_st", trade_date))
        return self.tables.get("stock_st", pl.DataFrame())

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
        return (self.tables.get("limit_list_d", pl.DataFrame())
                if limit_type == "U" else pl.DataFrame())


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


def test_stock_st_materializes_true_and_false_for_the_whole_daily_universe(
    warehouse, write_daily_bars
):
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ", "600000.SH"], "dt": [DAY, DAY],
        "open": [10.0, 20.0], "high": [10.0, 20.0], "low": [10.0, 20.0],
        "close": [10.0, 20.0], "volume": [1.0, 1.0], "amount": [1.0, 1.0],
    }))
    provider = FakeProvider(stock_st=pl.DataFrame({
        "ts_code": ["600000.SH"], "name": ["*ST浦发"],
        "trade_date": ["20260911"], "type": ["ST"],
        "type_name": ["风险警示板"],
    }))
    assert ingest_stock_st(provider, warehouse, [DAY]) == 2
    got = WarehouseQuery(warehouse).stock_st(None, DAY, DAY).sort("symbol")
    assert got["is_st"].to_list() == [False, True]
    assert got["name"].to_list() == [None, "*ST浦发"]


def test_empty_stock_st_response_is_explicit_false_not_missing(
    warehouse, write_daily_bars
):
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [10.0], "high": [10.0],
        "low": [10.0], "close": [10.0], "volume": [1.0], "amount": [1.0],
    }))
    assert ingest_stock_st(FakeProvider(), warehouse, [DAY]) == 1
    assert WarehouseQuery(warehouse).stock_st(None, DAY, DAY)["is_st"].to_list() == [False]


def test_empty_suspension_rerun_removes_a_stale_row(warehouse):
    provider = FakeProvider(suspend_d=pl.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["20260911"],
        "suspend_type": ["S"], "suspend_timing": [None],
    }))
    ingest_suspensions(provider, warehouse, [DAY])
    ingest_suspensions(FakeProvider(), warehouse, [DAY])
    assert WarehouseQuery(warehouse).suspensions(None, DAY, DAY).is_empty()


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
    assert ingest_instruments(provider, warehouse, date(2026, 9, 14)) == 2
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


# --- 名称快照（2026-09-14）-------------------------------------------------
#
# paid_daily 覆盖到 2026-09-11 为止，此后的交易日只有 namechange 一个源，
# 而它的 2006 断层让 96 只标的没有名称。stock_basic 给的是**当前**名称，
# 用它填历史就是结转；但用它填**取快照那一天**是直接观测。
# 所以把每天的快照累积成表，并显式记下观测日期。

def test_instruments_record_the_snapshot_date(warehouse):
    """instruments 是覆盖写的单张快照，必须知道它是哪天取的。

    没有 as_of 就无法判断 board / list_date / delist_date 有多陈旧，
    也无法安全地把 name 限定在快照日使用。
    """
    provider = FakeProvider(stock_basic_L=_basic_frame(["300750.SZ"], "L"))
    ingest_instruments(provider, warehouse, date(2026, 9, 14))
    got = WarehouseQuery(warehouse).instruments()
    assert got["as_of"].to_list() == [date(2026, 9, 14)]


def test_name_snapshots_accumulate_across_days(warehouse):
    """两天的快照都要留下——单张覆盖写的表只够用一天。

    daily_flags 每次同步都重建近 150 天，只有一天的快照解决不了问题。
    """
    provider = FakeProvider(stock_basic_L=_basic_frame(["300750.SZ"], "L"))
    ingest_instruments(provider, warehouse, date(2026, 9, 14))
    ingest_instruments(provider, warehouse, date(2026, 9, 15))

    got = WarehouseQuery(warehouse).name_snapshots(
        None, date(2026, 9, 1), date(2026, 9, 30)
    ).sort("dt")
    assert got["dt"].to_list() == [date(2026, 9, 14), date(2026, 9, 15)]
    assert got["symbol"].to_list() == ["300750.SZ"] * 2


def test_name_snapshot_rerun_on_the_same_day_is_idempotent(warehouse):
    """同一天重跑不能产生两行——双触发定时器每天会跑两次。"""
    provider = FakeProvider(stock_basic_L=_basic_frame(["300750.SZ"], "L"))
    ingest_instruments(provider, warehouse, date(2026, 9, 14))
    ingest_instruments(provider, warehouse, date(2026, 9, 14))

    got = WarehouseQuery(warehouse).name_snapshots(
        None, date(2026, 9, 14), date(2026, 9, 14)
    )
    assert got.height == 1
