"""全市场数据链路端到端：采集 -> 派生 -> 覆盖查询，全用构造数据不触网。"""

from datetime import date, timedelta

import polars as pl
import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.coverage import coverage
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.pipeline.universe import ingest_universe
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

SESSIONS = [date(2024, 1, 2) + timedelta(days=i) for i in range(5)]
BAR_SCHEMA = {
    "dt": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
    "amount": pl.Float64,
}


class ScriptedProvider:
    """第 1 只每天涨停，第 2 只炸板，其余平走。"""

    name = "scripted"

    def fetch_daily_bars(self, symbol, start, end):
        idx = int(symbol[:6]) - 600001
        rows = []
        price = 10.0
        for i, d in enumerate(SESSIONS):
            if idx == 0 and i > 0:
                close = high = round(price * 1.10, 2)
            elif idx == 1 and i > 0:
                high = round(price * 1.10, 2)
                close = round(price * 1.03, 2)
            else:
                high = close = price
            rows.append(
                {
                    "dt": d,
                    "open": price,
                    "high": high,
                    "low": price * 0.98,
                    "close": close,
                    "volume": 1000,
                    "amount": close * 1000,
                }
            )
            price = close
        return pl.DataFrame(rows, schema=BAR_SCHEMA)


def seed_instruments(warehouse: Warehouse, symbols) -> None:
    warehouse.write(
        "instruments",
        pa.table(
            {
                "symbol": list(symbols),
                "name": ["x"] * len(symbols),
                "market": ["SH"] * len(symbols),
                "list_date": [None] * len(symbols),
                "delist_date": [None] * len(symbols),
                "board": ["main"] * len(symbols),
            },
            schema=schema.INSTRUMENTS,
        ),
    )


@pytest.fixture
def rig(warehouse, write_daily_bars):
    symbols = [f"{600001 + i}.SH" for i in range(5)]
    seed_instruments(warehouse, symbols)
    ingest_universe(
        ScriptedProvider(),
        warehouse,
        symbols,
        SESSIONS[0],
        SESSIONS[-1],
        complete_through=SESSIONS[-1],
        batch_size=2,
    )
    # ingest_universe 只维护冻结的旧表 bars_daily；flags/regime 现在读付费源
    # paid_daily。把同一批刚写入的日线原样镜像过去，下游派生管线才有数据可读——
    # 这不是编造，是把 ingest_universe 已经落盘的真值复制到新的读取入口。
    bars = WarehouseQuery(warehouse).legacy_daily_bars(None, SESSIONS[0], SESSIONS[-1])
    write_daily_bars(warehouse, bars)
    build_daily_flags(warehouse, SESSIONS[0], SESSIONS[-1])
    build_market_regime(warehouse, SESSIONS[0], SESSIONS[-1])
    return warehouse, symbols


def test_all_symbols_are_ingested(rig):
    """验证 ingest_universe 自己的写入——它写的是冻结的旧表 bars_daily。"""
    warehouse, symbols = rig
    df = WarehouseQuery(warehouse).legacy_daily_bars(None, SESSIONS[0], SESSIONS[-1])
    assert df["symbol"].n_unique() == len(symbols)


def test_limit_up_flags_are_derived(rig):
    warehouse, _ = rig
    flags = WarehouseQuery(warehouse).daily_flags(SESSIONS[1], SESSIONS[1])
    hits = flags.filter(flags["is_limit_up"])["symbol"].to_list()
    assert hits == ["600001.SH"]


def test_broken_board_is_derived(rig):
    warehouse, _ = rig
    flags = WarehouseQuery(warehouse).daily_flags(SESSIONS[1], SESSIONS[1])
    broken = flags.filter(flags["is_broken_board"])["symbol"].to_list()
    assert broken == ["600002.SH"]


def test_first_board_only_on_the_first_limit_up_day(rig):
    warehouse, _ = rig
    flags = WarehouseQuery(warehouse).daily_flags()
    first = flags.filter(flags["is_first_board"])
    assert first["symbol"].to_list() == ["600001.SH"]
    assert first["dt"].to_list() == [SESSIONS[1]]


def test_market_regime_counts_match_the_flags(rig):
    warehouse, _ = rig
    regime = (
        WarehouseQuery(warehouse)
        .market_regime(SESSIONS[1], SESSIONS[1])
        .row(0, named=True)
    )
    assert regime["limit_up_count"] == 1
    assert regime["touched_limit_up_count"] == 2
    assert regime["broken_board_count"] == 1


def test_derived_tables_can_be_fully_rebuilt(rig):
    """派生数据不是独立事实——重算必须得到相同结果。"""
    warehouse, _ = rig
    before = WarehouseQuery(warehouse).market_regime()
    build_daily_flags(warehouse, SESSIONS[0], SESSIONS[-1])
    build_market_regime(warehouse, SESSIONS[0], SESSIONS[-1])
    after = WarehouseQuery(warehouse).market_regime()
    assert before.equals(after)


def test_resuming_after_a_kill_only_fetches_what_is_missing(rig):
    """采集跑很久，进程被杀后重跑必须接着跑。"""
    warehouse, symbols = rig
    again = ScriptedProvider()
    seen: list[str] = []
    original = again.fetch_daily_bars

    def spy(symbol, start, end):
        seen.append(symbol)
        return original(symbol, start, end)

    again.fetch_daily_bars = spy
    ingest_universe(
        again, warehouse, symbols, SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )
    assert seen == []


def test_coverage_lists_every_dataset(rig):
    warehouse, _ = rig
    names = {c.dataset for c in coverage(warehouse)}
    assert {"bars_daily", "daily_flags", "market_regime", "instruments"} <= names


def test_coverage_reports_the_right_range(rig):
    warehouse, _ = rig
    bars = next(c for c in coverage(warehouse) if c.dataset == "bars_daily")
    assert bars.first_dt == SESSIONS[0]
    assert bars.last_dt == SESSIONS[-1]
