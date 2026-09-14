"""端到端冒烟：用假 provider 走通完整链路（不触网）。

真实数据源的验证由 scripts/bootstrap_warehouse.py 手动执行。
"""

from datetime import date, datetime

import polars as pl

from qsys.core.calendar import AShareCalendar
from qsys.data.feed import Adjustment, HistoricalFeed
from qsys.data.pipeline.adjust import build_adj_factors
from qsys.data.pipeline.bars import ingest_daily_bars
from qsys.data.pipeline.instruments import build_instruments
from qsys.data.quality.report import assert_usable, run_quality_checks
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]

BAR_SCHEMA = {
    "dt": pl.Date, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
    "close": pl.Float64, "volume": pl.Int64, "amount": pl.Float64,
}

INSTRUMENT_SCHEMA = {
    "symbol": pl.Utf8, "name": pl.Utf8, "market": pl.Utf8,
    "list_date": pl.Date, "delist_date": pl.Date, "board": pl.Utf8,
}


class FakeBars:
    name = "fake-bars"

    def fetch_daily_bars(self, symbol, start, end):
        return pl.DataFrame(
            {
                "dt": SESSIONS,
                "open": [10.0, 10.1, 10.2],
                "high": [10.3, 10.4, 10.5],
                "low": [9.9, 10.0, 10.1],
                "close": [10.2, 10.3, 10.4],
                "volume": [1000, 1100, 1200],
                "amount": [10200.0, 11330.0, 12480.0],
            },
            schema=BAR_SCHEMA,
        ).filter(pl.col("dt").is_between(start, end))


class FakeReference:
    name = "fake-ref"

    def fetch_instruments(self):
        return pl.DataFrame(
            {
                "symbol": ["600036.SH"], "name": ["招商银行"], "market": ["SH"],
                "list_date": [date(2002, 4, 9)], "delist_date": [None], "board": ["main"],
            },
            schema=INSTRUMENT_SCHEMA,
        )

    def fetch_sessions(self, start, end):
        return [d for d in SESSIONS if start <= d <= end]

    def fetch_corporate_actions(self, symbol):
        return pl.DataFrame(
            schema={
                "ex_date": pl.Date, "cash_per_share": pl.Float64,
                "stock_per_share": pl.Float64, "rights_per_share": pl.Float64,
                "rights_price": pl.Float64,
            }
        )


def test_full_pipeline_produces_pit_queryable_warehouse(tmp_path, write_daily_bars):
    warehouse = Warehouse(tmp_path / "warehouse")
    calendar = AShareCalendar(SESSIONS)

    build_instruments(FakeReference(), warehouse, date(2026, 9, 14))
    written = ingest_daily_bars(
        FakeBars(), warehouse, ["600036.SH"], SESSIONS[0], SESSIONS[-1]
    )
    assert written == 3
    # ingest_daily_bars 只维护冻结的旧表 bars_daily；下面的复权/质检/PIT 读取
    # 都走 daily_bars()，现在指向付费源 paid_daily。把刚写入的日线原样镜像
    # 过去——同一份真值，只是补上新的读取入口，不是编造数据。
    #
    # 这座桥只存在于测试里：生产环境中 ingest_daily_bars（mootdx 路径）不会
    # 再喂给 build_adj_factors/run_quality_checks/HistoricalFeed——paid_daily
    # 永久只读、只能靠外部批量导入 parquet 分区，mootdx 写的是冻结的
    # bars_daily，两条链路在生产里永远不会衔接。此处的镜像是测试基础设施，
    # 不是被验证系统本身的行为。
    bars = WarehouseQuery(warehouse).legacy_daily_bars(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1]
    )
    write_daily_bars(warehouse, bars)
    assert build_adj_factors(warehouse) == 3

    query = WarehouseQuery(warehouse)
    report = run_quality_checks(query, calendar, ["600036.SH"], SESSIONS[0], SESSIONS[-1])
    assert_usable(report)
    assert report.is_blocked is False

    feed = HistoricalFeed(query, calendar)
    df = feed.get(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1],
        as_of=datetime(2024, 1, 3, 15, 0), adjustment=Adjustment.BACKWARD,
    )
    assert df["dt"].to_list() == [date(2024, 1, 2), date(2024, 1, 3)]
    assert date(2024, 1, 4) not in df["dt"].to_list()
