"""每日增量同步。拿不到数据必须显式失败，不许静默当成正常。"""

import importlib
from datetime import date

import polars as pl
import pytest

from qsys.data.pipeline import tushare_sync
from qsys.data.pipeline.tushare_sync import sync

DAY = date(2026, 9, 11)


class FakeProvider:
    """只实现 sync 真正会调的三个方法。

    多实现没用到的方法会掩盖「sync 调了不该调的接口」——每次调用都是积分。
    """

    def __init__(self, daily_rows: int = 1) -> None:
        self.daily_rows = daily_rows
        self.calls: list[str] = []

    def daily(self, d: date) -> pl.DataFrame:
        self.calls.append("daily")
        if self.daily_rows == 0:
            return pl.DataFrame()
        return pl.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": ["20260911"],
            "open": [11.76], "high": [11.79], "low": [11.69], "close": [11.70],
            "pre_close": [11.70], "change": [0.0], "pct_chg": [0.0],
            "vol": [100.0], "amount": [1000.0],
        })

    def daily_basic(self, d: date) -> pl.DataFrame:
        self.calls.append("daily_basic")
        return pl.DataFrame()

    def adj_factor(self, d: date) -> pl.DataFrame:
        self.calls.append("adj_factor")
        return pl.DataFrame()


def test_reports_row_counts_per_dataset(warehouse):
    got = sync(FakeProvider(), warehouse, DAY)
    assert got["tushare_daily"] == 1


def test_an_empty_daily_is_an_explicit_failure(warehouse):
    """日线 0 行 = 上游没给数据。静默当成正常正是 09-10/11 事故的根因。"""
    with pytest.raises(RuntimeError) as exc:
        sync(FakeProvider(daily_rows=0), warehouse, DAY)
    assert "20260911" in str(exc.value)


def test_an_empty_daily_writes_nothing(warehouse):
    """报错之前不能已经写了一半——半张表比没有表更难查。"""
    from qsys.data.query import WarehouseQuery

    with pytest.raises(RuntimeError):
        sync(FakeProvider(daily_rows=0), warehouse, DAY)
    # 日线族的查询方法强制显式区间（防止误读全历史，Task 3 曾因此 OOM），
    # 所以这里不能无参调用。
    assert WarehouseQuery(warehouse).tushare_daily(None, DAY, DAY).height == 0


def test_does_not_call_interfaces_it_does_not_need(warehouse):
    """每次接口调用都是积分。只该调 daily / daily_basic / adj_factor 三个。"""
    provider = FakeProvider()
    sync(provider, warehouse, DAY)
    assert sorted(provider.calls) == ["adj_factor", "daily", "daily_basic"]


def test_every_reference_ingest_is_wired_into_sync():
    """Task 4-8 新增的 ingest_* 必须接进每日任务，否则采集不到却不报错。

    计划 A 的 Task 8 踩过同一个坑：build_daily_flags 加了 boards 参数却没有
    生产调用点在传，派生表一天天被污染回去且不报错。

    tushare_reference 尚不存在时本条不生效——此时 _REFERENCE_STEPS 应为空。
    """
    wired = {name for name, _ in tushare_sync._REFERENCE_STEPS}
    try:
        ref = importlib.import_module("qsys.data.pipeline.tushare_reference")
    except ModuleNotFoundError:
        assert wired == set(), "参考模块还不存在，_REFERENCE_STEPS 应为空"
        return
    exported = {n for n in dir(ref) if n.startswith("ingest_")}
    missing = {n for n in exported if not any(fn.__name__ == n
                                              for _, fn in tushare_sync._REFERENCE_STEPS)}
    assert not missing, f"这些采集函数没接进每日同步：{sorted(missing)}"
