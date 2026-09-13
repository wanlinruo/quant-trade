"""每日增量同步。拿不到数据必须显式失败，不许静默当成正常。"""

import importlib
from datetime import date

import polars as pl
import pytest

from qsys.data.pipeline import tushare_sync
from qsys.data.pipeline.tushare_sync import last_open_day, sync

DAY = date(2026, 9, 11)


class FakeCalClient:
    """只实现 `.call`，用来喂 last_open_day 用到的 trade_cal 查询。

    不打真实网络：交易日历由测试自己摆，不依赖 Tushare 是否可达。
    """

    def __init__(self, cal_dates: list[str]) -> None:
        self.cal_dates = cal_dates
        self.params: dict | None = None

    def call(self, api_name: str, params: dict, fields: str = "") -> list[dict]:
        assert api_name == "trade_cal"
        self.params = params
        return [{"cal_date": d} for d in self.cal_dates]


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


def test_reports_row_counts_per_dataset(warehouse, monkeypatch):
    monkeypatch.setattr(tushare_sync, "_REFERENCE_STEPS", [])
    got = sync(FakeProvider(), warehouse, DAY)
    assert got["tushare_daily"] == 1


def test_an_empty_daily_is_an_explicit_failure(warehouse, monkeypatch):
    monkeypatch.setattr(tushare_sync, "_REFERENCE_STEPS", [])
    """日线 0 行 = 上游没给数据。静默当成正常正是 09-10/11 事故的根因。"""
    with pytest.raises(RuntimeError) as exc:
        sync(FakeProvider(daily_rows=0), warehouse, DAY)
    assert "20260911" in str(exc.value)


def test_an_empty_daily_writes_nothing(warehouse, monkeypatch):
    """报错之前不能已经写了一半——半张表比没有表更难查。

    这里不能只看「落盘之后没有文件」，也不能只给 Warehouse.write 打桩：
    compose_daily 对空 daily 本来就返回 0 行，write_year 对 0 行 frame 又是
    直接 `return 0` 不调用 Warehouse.write——所以即便把 sync() 改错成
    「先 compose_daily/write_year 再检查空」，Warehouse.write 的调用次数
    在两种写法下都是 0，这两条断言都会假装通过，抓不住重排序的 bug。
    真正会随「检查是否在写之前」变化的是 write_year 有没有被调用过
    （不管它内部是否真落盘），所以对 tushare_sync 模块里的 write_year
    引用打桩，断言它压根没被调用。
    """
    calls: list[tuple[int, int]] = []

    def fake_write_year(_warehouse, frame, year):
        calls.append((frame.height, year))
        return 0

    monkeypatch.setattr(tushare_sync, "write_year", fake_write_year)
    monkeypatch.setattr(tushare_sync, "_REFERENCE_STEPS", [])

    with pytest.raises(RuntimeError):
        sync(FakeProvider(daily_rows=0), warehouse, DAY)
    assert calls == []


def test_daily_stage_calls_exactly_its_three_interfaces(warehouse, monkeypatch):
    """每次接口调用都是积分。只该调 daily / daily_basic / adj_factor 三个。"""
    monkeypatch.setattr(tushare_sync, "_REFERENCE_STEPS", [])
    provider = FakeProvider()
    sync(provider, warehouse, DAY)
    assert sorted(provider.calls) == ["adj_factor", "daily", "daily_basic"]


def test_all_reference_datasets_have_a_daily_step():
    assert {name for name, _ in tushare_sync._REFERENCE_STEPS} == {
        "price_limits_daily", "suspend_daily", "index_bars",
        "instruments", "name_history", "limit_list_daily",
    }


def test_every_reference_ingest_is_wired_into_sync():
    """Task 4-8 新增的 ingest_* 必须接进每日任务，否则采集不到却不报错。

    计划 A 的 Task 8 踩过同一个坑：build_daily_flags 加了 boards 参数却没有
    生产调用点在传，派生表一天天被污染回去且不报错。

    tushare_reference 尚不存在时本条不生效——此时 _REFERENCE_STEPS 应为空。

    匹配用对象身份（`is`），不用 `fn.__name__ == n` 字符串比对：
    `functools.partial` 没有 `__name__`，字符串比对会直接 AttributeError
    崩测试；而且模块里随便一个同名影子函数也能让字符串比对误判为已接线。
    """
    try:
        ref = importlib.import_module("qsys.data.pipeline.tushare_reference")
    except ModuleNotFoundError:
        wired = {name for name, _ in tushare_sync._REFERENCE_STEPS}
        assert wired == set(), "参考模块还不存在，_REFERENCE_STEPS 应为空"
        return
    exported = {n for n in dir(ref) if n.startswith("ingest_")}
    missing = {n for n in exported
               if getattr(ref, n, None) not in tushare_sync._REFERENCE_INGESTS}
    assert not missing, f"这些采集函数没接进每日同步：{sorted(missing)}"


def test_last_open_day_when_today_is_a_trading_day():
    """今天在 trade_cal 的开市日集合里，就是今天。"""
    client = FakeCalClient(["20260909", "20260910", "20260911"])
    assert last_open_day(client, date(2026, 9, 11)) == date(2026, 9, 11)


def test_last_open_day_when_today_is_not_a_trading_day():
    """今天不开市（周末/节假日），取 trade_cal 里最近一个开市日。"""
    # is_open=1 的查询参数已经把非交易日过滤掉了，所以这里返回的列表
    # 本就不含「今天」——last_open_day 只管取最后一个。
    client = FakeCalClient(["20260909", "20260910", "20260911"])
    assert last_open_day(client, date(2026, 9, 13)) == date(2026, 9, 11)


def test_last_open_day_when_trade_cal_is_empty():
    """trade_cal 空 = 最近 30 天没有交易日（数据本身有问题），返回 None。"""
    client = FakeCalClient([])
    assert last_open_day(client, date(2026, 9, 11)) is None
