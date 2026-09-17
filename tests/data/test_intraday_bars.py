"""D-20：检查点报价与竞价量的统一分钟线读取。

- 付费分钟数据已发布覆盖的交易日读付费，否则读 QMT；同一天只用一个源。
- 付费 bar 边界比 QMT 晚几秒（2026-09-15 实测），保守地晚 1 分钟可见。
- 成交量统一为手；付费源是股。
- 竞价量只取 QMT 09:30 根或竞价逐笔撮合量，付费 09:30 根合并了开盘成交，不可用。
"""

from datetime import date, datetime, time

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.market_feed import MarketFeed
from qsys.core.calendar import AShareCalendar
from qsys.data.query import WarehouseQuery

PAID_DAY, QMT_DAY = date(2026, 9, 16), date(2026, 9, 17)
SYM = "600036.SH"


def _qmt(warehouse, day, times, closes, volumes):
    warehouse.write_day("minute_bars", pa.table({
        "symbol": [SYM] * len(times), "dt": [datetime.combine(day, t) for t in times],
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": volumes, "amount": [0.0] * len(times),
    }, schema=schema.MINUTE_BARS), day=day)


def _paid(warehouse, day, times, closes, shares):
    target = warehouse.root / "paid_minute_bars" / f"year={day.year}" / f"date={day:%Y%m%d}"
    target.mkdir(parents=True)
    pl.DataFrame({
        "symbol": [SYM] * len(times), "dt": [datetime.combine(day, t) for t in times],
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": shares, "amount": [0.0] * len(times), "factor": [1.0] * len(times),
    }, schema_overrides={"volume": pl.Int64}).write_parquet(target / "part.parquet")


def test_a_day_covered_by_paid_data_reads_only_paid(warehouse):
    _paid(warehouse, PAID_DAY, [time(11, 24), time(11, 25)], [10.0, 10.1], [1000, 2050])
    _qmt(warehouse, PAID_DAY, [time(11, 25)], [9.9], [7])  # 同日 QMT 不参与
    got = WarehouseQuery(warehouse).intraday_bars([SYM], PAID_DAY)
    assert got["source"].unique().to_list() == ["paid"]
    assert got["volume"].to_list() == [10.0, 20.5]  # 股 -> 手，不取整
    assert got["visible_at"].to_list() == [datetime(2026, 9, 16, 11, 25), datetime(2026, 9, 16, 11, 26)]


def test_a_day_without_paid_data_reads_qmt(warehouse):
    _paid(warehouse, PAID_DAY, [time(11, 25)], [10.0], [1000])
    _qmt(warehouse, QMT_DAY, [time(9, 30), time(11, 25)], [9.8, 9.9], [70, 7])
    got = WarehouseQuery(warehouse).intraday_bars([SYM], QMT_DAY)
    assert got["source"].unique().to_list() == ["qmt"]
    assert got["visible_at"].to_list() == got["dt"].to_list()


def _market(warehouse, moment):
    cal = AShareCalendar([PAID_DAY, QMT_DAY])
    return MarketFeed(WarehouseQuery(warehouse), cal).bind(moment)


def test_checkpoint_on_a_paid_day_never_sees_the_bar_ending_at_the_checkpoint(warehouse):
    _paid(warehouse, PAID_DAY, [time(11, 24), time(11, 25)], [10.0, 10.1], [1000, 2000])
    quote = _market(warehouse, datetime(2026, 9, 16, 11, 25)).intraday([SYM], PAID_DAY, time(11, 25))
    assert quote[SYM].price == 10.0 and quote[SYM].volume == 10


def test_checkpoint_on_a_qmt_day_uses_the_bar_ending_at_the_checkpoint(warehouse):
    _qmt(warehouse, QMT_DAY, [time(11, 24), time(11, 25)], [9.8, 9.9], [5, 7])
    quote = _market(warehouse, datetime(2026, 9, 17, 11, 25)).intraday([SYM], QMT_DAY, time(11, 25))
    assert quote[SYM].price == 9.9 and quote[SYM].volume == 12


def test_auction_volume_never_comes_from_paid_data(warehouse):
    _paid(warehouse, PAID_DAY, [time(9, 30)], [10.0], [9_348_500])
    assert _market(warehouse, datetime(2026, 9, 16, 9, 30)).auction_volume([SYM], PAID_DAY) == {}


def test_auction_volume_falls_back_to_auction_ticks(warehouse):
    warehouse.write("auction_ticks", pa.table({
        "symbol": [SYM, SYM], "dt": [PAID_DAY, PAID_DAY],
        "ts": [datetime(2026, 9, 16, 9, 24, 57), datetime(2026, 9, 16, 9, 25, 3)],
        "bid1": [10.0, 10.0], "ask1": [10.0, 10.0], "bid_vol1": [800, 900], "ask_vol1": [800, 900],
        "last_price": [0.0, 10.0], "volume": [0, 73743], "amount": [0.0, 737430.0],
    }, schema=schema.AUCTION_TICKS), year=2026)
    assert _market(warehouse, datetime(2026, 9, 16, 9, 30)).auction_volume([SYM], PAID_DAY) == {SYM: 73743}
